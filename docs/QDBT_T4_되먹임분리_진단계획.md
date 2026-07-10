# T4 되먹임 분리 — 진단 + 구현 계획 + 결정 필요 항목 (2026-07-10)

> 원칙: 안전·additive·되돌리기 쉽게 · 총액 불변(Δ=0) · create_app 정상 · 문법 통과 · 스키마 변경은 idempotent 자동 마이그레이션.
> 이 문서는 **보고용**입니다 — 결정 확정 전 코드 구현하지 않습니다.

## 1. 진단 (현재 상태)

### 1-1. 이미 분리된 것 — catalog 매칭 되먹임
- **`item_match` 테이블이 이미 존재**합니다(schema.py). 설계 주석에 T4 의도가 명시됨:
  "submission_items=견적 원본(확정 시 동결), item_match=비교 결과(자유 갱신), 되먹임이 원본을 건드리지 않는다."
- 컬럼: `item_id, catalog_item_id, match_status, match_confidence, match_note, version(0=현재), updated_at`, PK `(item_id, version)`.
- `accept_cluster()`가 `_im_upsert()`로 **item_match에 기록** → 카탈로그 연결/매칭 상태는 정본을 안 건드림.
- submission_items의 legacy 컬럼 `catalog_item_id / match_status / match_confidence / match_note`:
  **write 지점 0건**(전 소스 grep) → 사실상 미사용(dead). ⇒ 카탈로그-매칭 되먹임은 T4 목표 달성됨.

### 1-2. 아직 남은 되먹임 — `category`(+`path`) write-back
비교/클러스터 조작이 **submission_items.category(정본)를 직접 UPDATE**하는 지점:
| 위치 | 기능 | 쓰는 컬럼 |
|---|---|---|
| `catalog_clusterer.py:change_cluster_category` | 클러스터 분류 변경(멤버 전원 category 일괄 변경) | category |
| `compare.py:move_items_to_category` (≈656) | 미분류 항목을 특정 분류로 이동 | category |
| `compare.py` (≈702, 872) | 분류 이동/동기화 | category |
| `queries.py:apply_category_binding` (≈2518) | "표준 분류로 재지정" | category, path |

- 원인 구조: **클러스터 category는 저장값이 아니라 멤버 다수결 파생값**(catalog_clusters에 category 컬럼 없음). 그래서 "클러스터 분류 변경"을 멤버들의 submission_items.category를 바꾸는 방식으로 구현 → 정본 오염.

### 1-3. 정본 편집 vs 되먹임 경계(중요)
- `submissions.py`의 `UPDATE submission_items SET path,depth,category`(≈1520/1842/1910/1928)는 **/link 연계 캔버스의 재부모화(병합 WS)** — 즉 **확정 전 정본 편집**이라 정당함(되먹임 아님).
- 반면 1-2의 compare/cluster 쪽은 **비교 WS**의 사후 조작 → 되먹임(정본이 바뀌면 안 됨).
- ⇒ 경계는 **WS + 시점**: 병합 WS·확정 전 = 정본 편집 허용 / 비교 WS·확정 후 = override로 분리.

## 2. 구현 계획 (논리 분리 우선 → 물리 승격)

### 2-1. 목표
비교 WS의 category 변경이 **정본(submission_items)을 건드리지 않고 override 계층**에 쌓이게 하고,
읽기 경로에서 `유효 category = override ?? 정본 category`로 병합.

### 2-2. 단계
1. **서비스 경계 신설(논리 분리)**: 모든 category 변경을 단일 함수(예: `set_item_category(item_id, cat, *, source)`)로 통과. source ∈ {merge(정본), compare(override)}.
   - source=merge → 지금처럼 submission_items 갱신(확정 전만).
   - source=compare → override에 기록(정본 불변).
2. **override 저장소**: (결정 필요 — 아래 3-①) 우선 후보 = item_match에 `category_override` 컬럼 1개 추가(이미 비교 WS 소유 테이블, version 차원 재사용). idempotent ALTER 자동 마이그레이션.
3. **읽기 경로 병합**: `compare_bid_submissions`·`build_items_tree`·`list_submission_items_for_clustering`의 category 읽기를 `COALESCE(override, 정본)`로. (총액·경로 로직 불변 → Δ=0 보장)
4. **확정 게이트 연동(T9/T10)**: 확정(confirm) 상태 정의 후, 확정된 제출서의 정본 category write를 잠금(서비스 경계에서 가드). T4는 "경계·override까지", 잠금은 T10.

### 2-3. 마이그레이션 없이 가능한가?
- item_match 테이블은 이미 자동 생성됨. **컬럼 1개 추가**는 기존 auto-migrate 패턴(schema.py의 ALTER 누적)으로 idempotent 처리 가능 → 사실상 "마이그레이션 없이"(무중단·additive).
- 데이터 이관 불필요(override는 신규, 없으면 정본 사용).

## 3. 결정 필요 항목 (착수 전)

① **override 저장 위치**:
   (a) item_match에 `category_override` 컬럼 추가(권장 — 비교 WS 소유·version 재사용) /
   (b) catalog_clusters에 클러스터 단위 category 저장(미분류 항목은 못 담음) /
   (c) 별도 override 테이블.

② **정본 편집 허용 경계**: 병합 WS(/link)·확정 전 category/path 변경은 **정본 직접 편집 유지**가 맞는가? (권장: 유지. 확정 후만 override)

③ **"표준 분류로 재지정"(apply_category_binding) 처리**: 이건 정본 교정 성격 vs 비교 override 성격? (권장: 확정 전=정본 교정, 확정 후=override)

④ **legacy 컬럼(submission_items.catalog_item_id/match_*)**: 지금 제거(마이그레이션) vs 유지(권장: 유지, 물리 분리 승격 시 정리).

⑤ **범위**: 이번 T4에서 category까지만 override화할지, path·기타까지 확장할지. (권장: category 우선, path는 함께 필요 시)

## 4. 리스크·검증 계획
- Δ=0: override는 category(분류 라벨)만 — 금액/수량/단가 불변이므로 총액 회귀 없음. 15샘플 + 3차 입찰 재검증.
- 읽기 병합 누락 방지: category 읽는 모든 경로 목록화 후 일괄 COALESCE.
- 되돌리기: override는 삭제만으로 원복. 스키마는 additive.
