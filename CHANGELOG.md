# QDBT Changelog

이 프로젝트의 모든 주요 변경 사항을 이 파일에 기록합니다.

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버전은 [유의적 버전(SemVer)](https://semver.org/lang/ko/)을 준수합니다.

## [Unreleased] — 엉망 견적서 견고성 테스트 + 파이프라인 5건 수정

> 검증: `tests/rob/run_matrix.py`(S1~S7, 함수 직접 호출·서버 없음) 36/36 PASS,
> 실샘플 15개/31시트 스티칭 총액 **Δ=0**(C2 band 1.65B·C3 seq 1.677B 등 회귀 없음).
> 상세 매트릭스: `docs/QDBT_견고성테스트_20260711.md`.

### 원칙 — 항목 연결 3단계 (코드 → LLM → 사람) *(반드시 유지)*
- **1단계 완전합치(코드)**: 스티칭(무LLM 결정론)이 담당. 시트 내/시트 간 **완전동일**
  (정규화 분류경로+품명+규격+금액 정확일치)만 자동 병합 — `stitch._dedup_and_rollup` (A) 완전
  중복 dedup + (B) 요약행 roll-up.
- **2단계 유사도(LLM)**: 완전합치 안 된 **유사** 항목의 연결 '제안'. 스티칭이 아니라 DB 저장
  후 기존 LLM 파이프라인이 담당 — `extractors.catalog_clusterer`(코드 exact prepass → LLM
  유사도 클러스터), `extractors.matcher`(LLM 카탈로그 매칭 `match_status='suggested'`).
- **3단계 사람 점검**: LLM 제안을 담당자가 최종 확인·수정(accept/reject 확정 플로우).
- ⚠️ 스티칭이 시트 간 '유사(비완전합치)'를 코드로 자동 병합하거나 '미연계'로 **종결 표기**하면
  2단계(LLM)를 건너뛴 오합치·오종결이다. → 유사 항목은 **별도 정본 잎으로 보존(총액 Δ=0)**해
  DB에 저장하고, 유사도 연결은 2·3단계에 위임한다. (원칙 배너: `src/extractors/stitch.py`)
- **경로 검증(DB 왕복)**: 유사 잎이 실제로 다운스트림 LLM 단계로 흘러감을 확인 —
  `insert_items_bulk`→`is_header=0` 저장→`list_submission_items_for_clustering`·`matcher` 모두
  두 유사 잎을 반환(경로 단절 없음). 시트간 동일 품명·규격·금액/다른 분류경로 2건이 클러스터링·
  매칭 입력에 모두 도달 → LLM 유사도 제안 대상.

### Fixed — 스티칭/파싱 견고화 (견고성 테스트에서 발견)
- **합계행 미탐 → 이중계상** (`stitch._is_total_row`·`extract.detect_total_rows`): 합계 마커가
  품명 셀에 있고 분류 셀 값이 조인 뒤에 붙는 경우(`"소계 재료비"`) `is_total_label` 의
  `endswith` 앵커가 무력화돼 소계·합계 행이 잎으로 계상됐다 → 조인 검사 + **셀 단독 검사** 보강.
  실샘플 15개/31시트/3446행 per-cell 신규발동 0건(Δ=0). (병합 대분류가 소계행에 채워지는 흔한 양식)
- **seq 모드 leaf/breakdown 시트 은닉 소실** (`stitch._run_stitch`·`_dedup_and_rollup`): 정수번호+
  금액 시트(role `leaf`, 예 갑지·공종목록)가 점계층 세부와 함께 오면 `seq_*` 만 방출돼 통째
  소실됐다 → leaf 시트도 방출 + **전체요약 roll-up**(시트 총액=상세 정본 총액이면 전 행 roll-up)
  으로 요약은 제외·독립 breakdown은 residual 보존. C3 공종목록(대분류 요약, 상세와 이름 불일치)도
  총액 일치로 roll-up → C3 Δ=0.
- **band 요약 고유비용 소실** (`stitch._emit_uncovered_band_costs`): band 요약 행 중 '완전 고립'
  (분류값이 어떤 잎 경로에도 미등장, 예 부대비)만 잎+residual로 보존→총액 누락 방지. 한 값이라도
  잎과 공유하면 계층 일부(빈 카테고리 소계)로 보고 미방출 → C2 Δ=0(전부 커버 시 무방출).
- **로케일 모호 파싱** (`core.numparse.parse_amount`): 단일 점 + 정확히 3자리 소수부 → 유럽식
  천단위(`'1.000'→1000`; `'3.5'`·`'0.125'` 소수 유지). `'1-5'`/`'1~5'` 범위표기는 억지 숫자화(15)
  대신 `None`(미상) 보존해 오계상 방지(계층번호는 seq 역할 열의 `seq_tuple` 처리). 실샘플 Δ=0.

## [0.9.11] - 2026-07-10 — 무품명·비연속레벨·번호계층 견고화 + 파이프라인 취약점 일괄 수정

> 회귀 게이트(모든 항목 공통): 단일시트 샘플 총합 **Δ=0**(62,680,494,000), 스티칭 실샘플
> 총액 **Δ=0**, `create_app` 133라우트 정상, 전 모듈 `py_compile` 통과. 통화·총계행 관련
> 일부 auto-stitch 샘플은 **기존 버그값이 정확값으로 교정**(아래 명시).

### Fixed — 추출/스티칭 견고화 (전반부 세션)
- **품명(name) 열 미지정 추출 실패**: 분류+금액만 있는 시트가 `is_category_header`로 전부
  빠져 총액 0이던 것 → 최말단 분류를 잎으로 승격(단일시트·다중시트 양쪽). leaf 없는 다중시트
  0건, seq 모드 다중 세부시트·seq_list 자체금액 소실도 함께 해소. (`extract_by_mapping`, `stitch`)
- **비연속 분류 레벨(gap) 미보존**: `[중분류,세분류]`처럼 중간 레벨이 비면 세분류가 소분류
  자리로 당겨지던 것 → 절대 레벨 배치 + 상단·중간 gap을 `⟨미연계·N분류⟩` placeholder로 충전.
  교차시트 정렬(`_cross_sheet_reparent`)을 2-pass로 바꿔 시트 간 같은 레벨을 같은 depth로 정렬.
- **번호(seq) 계층이 점(.)만 인식**: 하이픈·공백·가운뎃점 등 어떤 구분자든 "숫자 그룹 튜플"로
  해석(구분자 무관, `seq_tuple`/`_SEQ_TUPLE_RE`). 원본 문자열은 변형하지 않음.

### Fixed — 파이프라인 취약점 코드리뷰 일괄 수정 (`docs/QDBT_코드리뷰_취약점_20260710.md`)
- **숫자·통화 파싱 통합** (`core/numparse.py` 신규): extract·queries의 별개 `_to_number`를
  단일 `parse_amount`로 통합. 통화기호(₩$¥€元£ 등) 제거, 괄호·△·▲·−(U+2212)=음수(부호 보존),
  유럽식 소수 표기, 전각(NFKC) 처리. (기존엔 ¥·€ 금액이 통째로 소실·괄호음수가 양수로 뒤집힘)
- **외화 원화정합 공용화 + 스티칭 반영**: 통화 처리(`apply_currency_fields`)를 extract·stitch가
  공유. 환율 미확정 외화는 `needs_fx=True`+원화 None으로 KRW 합계 오염 방지. 스티칭 `_read_records`가
  통화/원화 열을 읽음. (교정: D엔지니어링 976,583,400→2,277,532,000 = CCY 혼합합산→Amount(KRW),
  A상사 0→2,289,400,000 = amount_krw 무시 해소)
- **합계행 판정 견고화**(`is_total_label`): "합계 : 1,200,000"·"합 계 (VAT 별도)"의 뒤 값/괄호
  주석 제거 후 판정(과소탐지 해소). 영문 단독 grand/sum 제외(제품명 오탐 방지), CJK 合計/小計 추가.
  (교정: F글로벌·G테크의 괄호 달린 총계행 이중계상 제거)
- **헤더 자동매핑 부분일치 오탐**(`suggest_column_mapping`): 짧은 토큰(no/item 등) 단어경계 매칭
  (`Notes`가 seq로 오인되던 것), `quantity` 인식 추가, amount 가드의 '원'을 통화표기로 한정.
- **LLM 경로 방어**: 응답을 `content[0].text` 가정 대신 text 블록만 결합(추론형 모델 전면 실패
  방지), JSON을 코드펜스·서두산문·후행텍스트 허용 추출(`core/llm_json.py`), gpt content=None 방어,
  max_tokens 16000→32000, 매칭 temperature=0, 클러스터 score 파싱 실패 시 행 단위 격리.
- **스티칭 nego 시트경계**: 다중시트 nego가 다른 시트 동일 행을 오차감하던 것 → (시트,행) 쌍 매칭.
- **residual/candidate 무통보 절단**: 상한 상향(200/1000) + `*_truncated` 플래그로 UI 통지.
- **집계**: 죽은 코드·domain 중복정의 제거, path 구분자 통일(`_split_path`/`_path_under`), dedup
  카테고리 substring 오매칭 방지, 최저가 판정에 0원 제안 포함.
- **LLM 파이프라인 환율도출**을 USD 전용·밴드 500~2000 → 통화별 일반화. `set_fx_rate` 양수 검증.

### Changed
- **추출 프롬프트 도메인/통화 중립화**(`docs/prompt_extract_v1.md`, `llm_extractor`): "한국 IT" 및
  고정 카테고리 5종·"KRW|USD" 강제 제거 → 견적서에 적힌 분류·통화를 그대로 사용하도록.
- 레벨명 배열(`대분류`…`세부`) 단일 상수화 + 깊이 초과 시 `N레벨` 폴백.
- `line_no` 파싱을 `int(ln[1:])`(첫 글자 제거) → `_line_row`(정규식 숫자부 추출)로 통일.
- 앱 표시 버전 `config.VERSION` 0.9.4 → 0.9.11.

### Added
- `docs/QDBT_코드리뷰_취약점_20260710.md`: 추출·비교 파이프라인 취약점 진단 보고서(HIGH 10/
  MEDIUM 20/LOW 9).
- `src/core/numparse.py`(숫자·통화 파서), `src/core/llm_json.py`(LLM JSON 견고추출).

## [0.9.10] - 2026-07-09 — 다중시트 중복 병합(요약↔상세, 총액 불변) + 4단계 연계 위저드

### Added (연계 캔버스 — 목업 4단계 완성, `src/web/templates/submissions/link.html`)
- **스텝퍼(1~4)**: 추출 → 정합성 제안 → 수기 조정 → 확정 데이터. 목업 `QDBT_다중시트연계_목업.html` 정합.
  - **1단계 추출**: 시트별 인식 역할(세부/밴드/목록/트리) 표시, 스티칭 모드·중복 병합 요약.
  - **2단계 정합성 제안**: 자동 연계를 **파선 SVG**로 시각화(읽기 전용).
  - **3단계 수기 조정**: 미연계(빨강) 잎을 상위 분류로 **드래그**(reassign-path), **실선 SVG**, 검증바(소계/연계 정합·총액).
  - **4단계 확정 데이터**: 통합 트리 + **버전 스냅샷**(확정 시 동결, 수정·재확정 시 v2 승격).
- **확정 버전 스냅샷**: `submission_snapshots` 테이블 + `POST /submissions/<id>/link/confirm`. 통합 트리를 버전별로 동결 보존(금액 불변, 표시/이력용).



### Fixed
- **견적서(요약)+상세 중복 계상 버그** (`src/extractors/stitch.py`): 갑지(요약) 시트에 수량/단가 열이 있으면 상세 시트와 함께 둘 다 `leaf`로 분류돼 **금액이 2배로 합산되고 트리에 두 번** 표시되던 문제. `_dedup_and_rollup` 후처리 신설 —
  - (A) **완전중복 잎**: 서로 다른 시트의 (분류경로+품명+규격+금액) 동일 잎 → `duplicate` 플래그(깊은 시트가 정본).
  - (B) **요약행 roll-up**: 얕은(요약) 시트 행 금액이 상세 카테고리 잎 합계와 정합하면 → `rolled_up` 플래그(**상세 잎이 정본**). 매칭 기준: 카테고리 대응 + 금액 체크섬(±0.5%). 정합될 때만 제외 → **총액 불변**.
  - **비파괴·되돌리기**: 제외행은 삭제하지 않고 `merge_status`(`rolled_up`/`duplicate`) + `is_header=1`로 저장 → 모든 합계 쿼리에서 자동 제외되며, 플래그 해제(`is_header=0`·`merge_status=NULL`)로 원복 가능.
  - **안전장치**: 요약 시트라도 한 행도 정합 안 되면 '독립 상세 시트'로 보고 전량 보존(자재내역+인력내역 같은 분리 시트 오병합 방지). 미매칭 요약행은 잎 유지 + residual 표시.
- **다중시트 라우팅** (`src/web/blueprints/submissions.py`): `mount_path` 미지정 다중 시트를 스티칭 엔진으로 통합 추출하도록 자동 판정 확장 → 총계행 정확 제외 + 중복 병합 적용. 외화(통화 환산) 매핑이 있으면 원화 파이프라인 보존 위해 기존 T6 경로 유지. 응답 `n_items`는 병합 제외행을 뺀 정본 잎 수로 보고.

- **중복 병합 리포트**(상세 화면 배너, `detail.html`): 요약행 N건이 상세와 정합해 총액에서 제외됨을 표시(총액 불변 명시), 제외 내역(요약행 = 상세 카테고리 합) 나열.

### Notes
- 스키마 변경 2건(idempotent, 재기동 시 자동 마이그레이션): `submission_items.merge_status TEXT`, `submission_snapshots` 테이블.

## [0.9.9] - 2026-07-09 — T7 다중시트 스티칭·연계 + 매핑보존 + 부품/메이커

### Added
- **T7 다중시트 스티칭 엔진** (`src/extractors/stitch.py`): 겹치는 밴드([대/중/소]·[중/소/세]·[세/품목])·목록/트리/세부(번호계층)·올인원을 하나의 잎-보존 트리로 조립. 자동 조인(공유레벨/seq) → 미해결(residual) 산출.
- 열매핑 **원클릭 "시트 선택 통합 추출"**(auto-stitch): 시트 선택칸 상시 표시, 갑지·설명 시트 자동 해제. 저장된 시트별 매핑을 사용.
- **시트 매핑 보존**: 시트 전환 시 자동 드래프트 저장(`/map/save-sheet`), 재진입·통합추출이 복원·사용.
- **residual 수기 보정 UI**(상세): 미연계 항목의 상위 분류 재지정(reassign-path), LLM/휴리스틱 제안, 일괄 적용, 표준분류 직접 추가.
- **다중시트 연계 캔버스** `/submissions/<id>/link`: 레벨별 열에서 미연계 잎을 상위 분류로 드래그해 연결(목업 기반).
- **메이커(제조사)** 필드: 스키마 `submission_items.maker` + 열매핑 역할 + 트리 🏷 표시. 가지(레벨)엔 대표 메이커(균일 시)·"혼합" 표시.
- **부품 레벨**: 열매핑 "부품" 지정 시 품목을 분류 경로로 내리고 부품을 잎으로(품목당 N개). 스키마 무변경.
- **기능단위 비교 설정**: compare_units를 중/소분류 레벨로 일괄 지정.
- 비교 단위 트리 **가지(분류) 삭제**. "○○ 외 N종" **요약 항목 탐지**(비파괴 residual).

### Fixed
- 대량 시트의 'No' 행번호 열이 seq로 오인식돼 대/중/소/세 계층이 붕괴되던 버그(점 계층 있을 때만 seq 모드).
- 드래그로 기존 클러스터 이동 시 분류가 '자재'로 오염(리셋해도 유지)되던 버그(신규 클러스터 생성 시에만 category 적용).
- subtotal 자동 제외가 현재 시트 매핑으로만 판단하도록 수정.
- 비교 단위 저장 버튼 피드백 미표시(버튼 즉시 피드백 + 에러 처리).

### Notes
- **레벨별 단가 = 하위 잎 amount 합(roll-up)** 으로 확정(별도 저장 불필요, 기존 소계 집계와 동일).
- 스키마 변경은 `maker` 컬럼 1건(idempotent, 재기동 시 자동). 설계 상세: `docs/QDBT_C설계메모_20260708.md`.

## [0.9.88] - 2026-07-07

### Fixed
- 업체별 비교에서 클러스터 다수결 분류로 인해 자재 품목이 인건비로 표시되던 문제 완화 (분류 섞인 클러스터에 ⚠분류혼재 경고 배지·분포 툴팁 추가, 개별 항목은 원본 분류 존중)
- price_history project_name NULL 저장 (accept_cluster projects 조인 누락 — 맥북 Claude Code 정본 반영)

## [0.9.87] - 2026-07-07

### Fixed
- 업체별 비교 엑셀 추출 시 클러스터 시트에 금액이 아닌 단가가 들어가던 문제 (amount 기준으로 수정)

### Changed
- 엑셀 보고서 구조 재편: 요약 → 클러스터 → 업체별 세부(각 업체 별도 탭, 전체 항목 테이블 구조)
- 카테고리별 비교 시트 제거 (클러스터·업체별 세부로 대체)

## [0.9.86] - 2026-07-07

### Fixed
- 비교 단위 트리에서 항목 삭제 시 화면이 갱신되지 않던 문제 (존재하지 않는 rebuildTree 참조 조건 제거, 삭제 성공 시 항상 새로고침)
- 업체별 비교에서 범주 이동 시 항상 '자재'로 이동되던 문제 (moveForm 내 name="category" 중복 필드 제거)
- 비교 단위 트리에 환율 적용된 단가가 표시되지 않던 문제 (잎 노드에 "단가 × 수량" 표기 추가)

### Added
- 다중 시트 누적 추출 백엔드 (삭제 1회 + 시트별 매핑 누적 삽입, map_config v2)
- 합계 검증 공통 모듈 (core/totals.py — 4종 불변식 단일 창구)
- item_match 테이블 (견적 원본/비교 결과 물리적 분리, 되먹임 분리)
- schema_meta 테이블 (1회성 보정 추적)
- amount_orig 컬럼 (단가 없는 외화 행의 원통화 보존)
- 환율 삭제 기능 (통화 환율 제거·원통화 복귀)

### Changed
- 환율 재계산 시 amount = 단가 × 수량 불변식 보존
- 도메인 보정 마이그레이션 1회성화 (schema_meta 가드)
- 클러스터 순서 드래그 시 행 그룹 전체 이동 (테이블 섞임 해결)

카테고리 규칙: `Added`(추가) · `Changed`(변경) · `Fixed`(수정) ·
`Deprecated`(폐기 예정) · `Removed`(제거) · `Security`(보안).
각 항목은 변경이 일어난 파일을 괄호로 명시하고, 필요 시 원인·조치를 함께 적습니다.

> 이 파일은 커밋 단위로 관리합니다. 새 변경은 최상단 `[Unreleased]`에 쌓고,
> 릴리스 시 버전·날짜를 부여해 아래로 내립니다.

---

## [Unreleased]

### 예정
- **그룹 D — 이모지 정리**: 상태·의미 이모지는 텍스트·기호로 대체, 장식용(로봇 아이콘 등) 제거 (약 28개 파일)

---

## [0.9.7] — 2026-07-03 — 비교표 UX 고도화 · 통화 파이프라인 · 클러스터링 정합

v0.9.6에서 이어진 13개 개선 항목(그룹 A·B·C)과, 실사용 중 발견된 통화·클러스터링
결함의 근본 수정을 포함한다. 그룹 D(CHANGELOG·이모지)는 진행 중.

### Added

- **기준정보 도메인 바인딩** (그룹 A) (`queries.py`, `admin.py`, `projects.py`, `admin/attr_defs.html`, `projects/detail.html`)
  - `list_attr_defs(active_only, domain=None)`에 도메인 필터 추가 — `'공통'` 항목은 전 도메인 공유, 도메인 전용 항목은 해당 도메인 프로젝트에만 노출
  - 기준정보 관리 화면에 소속 도메인 선택(추가·편집)·표시 열 추가
  - 프로젝트 상세 → "기준정보 항목 관리" 이동 버튼, 관리 화면 → "프로젝트로 돌아가기" 복귀 링크(`return_project`)로 왕복 동선 완성
  - 항목 관리(추가·삭제·변경)와 값 입력을 구분하는 안내 문구 추가
- **분류 배지 인라인 드롭다운** (그룹 B) (`compare/bid.html`)
  - 배지 클릭 시 중앙 모달 대신 배지 바로 아래 인라인 드롭다운 노출(`#qdCatInlinePop`), 선택 즉시 분류 이동
  - 분류 목록·현재 분류 표시(✓)·구분선·"＋ 신규 분류 만들기" 포함, 기존 `qdCatChangeForm` 재사용
- **분류 순서 재정렬·되돌리기** (그룹 B) (`schema.py`, `queries.py`, `compare.py`, `compare/bid.html`)
  - `bids.category_order`(JSON) 컬럼 추가 — 입찰별 분류 표시 순서 저장
  - `category_order_for_bid`가 저장 순서 우선 적용(없으면 분류관리 기본순서), `set_bid_category_order`·`reset_bid_category_order` 추가
  - 분류 헤더 드래그 핸들(⋮⋮)로 순서 변경, "↺ 기본순서로" 버튼으로 분류관리 기본순서 복원
  - 라우트 `reorder_categories_route`, `reset_categories_order_route` 추가
- **분류 내 금액 정렬** (그룹 B) (`compare/bid.html`)
  - 분류 헤더의 "금액↑/금액↓" 버튼으로 해당 분류 내부 클러스터를 금액순 정렬(분류 경계 유지)
  - 클러스터 행에 `data-cl-amount`(최저 총액) 부여, `qdSortCatByAmount`로 그룹 단위 재배치
- **트리 경로 통일 필터** (그룹 C) (`app.py`)
  - `treepath_above(path, name)` 필터 신설 — 품명과 같은 잎(마지막 세그먼트)을 제거해 모든 업체가 "품명 바로 위 단계"까지 일관 표시
- **통화(외화) 처리 파이프라인** (`extract_by_mapping.py`, `submissions/column_map.html`, `queries.py`, `schema.py`, `app.py`, `compare/bid.html`)
  - 추출 역할에 `currency`(통화)·`price_krw`(원화단가)·`amount_krw`(원화금액) 추가 (`INFO_ROLES`)
  - 통화 코드 정규화($·¥·€·₩ → ISO), 환율 역산(`환율 = 원화 ÷ 통화`), 원화 확정(견적서 원화열 우선)
  - `suggest_column_mapping`에 통화·원화 헤더 키워드 + `ROLE_PRIORITY`(구체적 역할 우선 매칭) 추가 — "Amount (CCY)"↔"Amount (KRW)" 구분
  - 열 매핑 화면 드롭다운(`ROLES`)에 통화·원화단가·원화금액 옵션 추가
  - `submission_items.fx_rate_used`(항목별 적용 환율) 컬럼 추가, `insert_items_bulk`에 저장
  - `cursym` 필터(KRW·USD·CNY·EUR·JPY 기호 매핑) 추가, 비교표에 원화기준(₩) + 외화 2단(환율·통화단가) 표시

### Changed

- **비교표 열 정렬** (그룹 C) (`compare/bid.html`)
  - `table-layout:auto` → `fixed` + `<colgroup>`으로 헤더·본문 열 너비 강제 정합, `width:max-content`로 최저열을 마지막 업체열에 밀착
  - 본문 td의 개별 width 지정 제거(colgroup 위임), 열 너비 조절(`qdSetColW`)은 CSS 변수로 유지
- **트리 표시 위치·통일** (그룹 C) (`compare/bid.html`)
  - 트리 경로를 품명 옆(인라인)에서 품명 아래 줄로 이동, 3개 지점(업체 셀·미분류 행·미분류 세부) 모두 `treepath_above` 적용
- **클러스터 정렬 드래그 피드백** (그룹 C) (`compare/bid.html`)
  - 드래그 중 원본 행 강조(`qd-sort-src`), 드롭 위치 표시선(`qd-sort-over-top/bot`), grabbing 커서(`qd-sorting`), `dragend` 정리 추가
- **제출서 대표 환율 집계** (`queries.py`)
  - `recompute_subtotal`이 항목별 통화·환율에서 제출서 대표 환율(최빈값)·외화 포함 여부(`has_usd_items`) 집계 — 비교 화면 환율 표시용
- **환율 표시 일반화** (`compare/bid.html`)
  - 기존 USD 하드코딩(`== 'USD'`) 제거, 모든 외화(USD·CNY·EUR 등)에 통화 기호 자동 매핑으로 표시

### Fixed
- 환율 재계산 시 amount = 단가 × 수량 불변식 보존 (독립 환산으로 라인 정합이 깨지던 버그 수정)

- **클러스터링 잎 매칭 정합** (`queries.py`)
  - 원인: `list_submission_items_for_clustering`의 묶기 매칭이 잎의 상위 경로(`path`)만으로 사용자 지정 `compare_units`(잎까지 완전 경로)를 대조 → 트리가 깊은 업체(예: `자재 > 서버 장비 > GPU 가속 서버`)에서 잎이 공통 상위(`서버 장비`)로 뭉쳐, 얕은 업체의 잎과 레벨 불일치 → 최초 클러스터링 실패·오병합(한 묶음이 여러 클러스터에 중복 배정)
  - 조치: 매칭 기준을 잎의 완전 경로(`path + " > " + name_normalized`)로 변경 → 사용자가 지정한 비교 단위(잎/중분류)가 그대로 반영, 데이터는 항상 잎까지 보존
- **열 매핑 화면 500 오류** (`extract_by_mapping.py`)
  - 원인: `column_map` 라우트가 import하는 `suggest_column_mapping` 함수가 이전 `detect_total_rows` 추가 시 같은 위치에서 덮어써져 소실 → `ImportError`
  - 조치: 원본에서 헤더 키워드 매칭 로직 복원(`detect_total_rows` 앞에 재배치)
- **분류 배지 클릭 무반응** (`compare/bid.html`)
  - 원인: `onclick`의 `{{ ...|tojson }}`이 HTML 속성 큰따옴표와 충돌해 `SyntaxError`
  - 조치: 배지를 `data-*` 속성 + 이벤트 위임 방식으로 전환(특수문자·따옴표에 견고)
- **통화 비교 이종통화 왜곡** (`queries.py`, `schema.py`, `compare/bid.html`)
  - 원인: 통화 추출 미완성으로 원화 미저장 → 이종통화(CNY vs KRW)가 원화 환산 없이 비교됨
  - 조치: 추출 시 원화 확정·환율 역산 저장, 소계·클러스터 금액이 원화(`amount`) 기준으로 집계되도록 정합 → 원화 기준 통일 비교

---

## [0.9.6] — 2026-07 — 도메인 확장 기반 · 비교표 정비

전 도메인(설비·용역·IT 등) 확장을 위한 데이터 모델과 비교표 기반 정비.

### Added

- **도메인 모델** (`schema.py`, `queries.py`)
  - `domains` 테이블, `projects.domain`·`bids.domain` 컬럼 추가 — 프로젝트·입찰의 도메인 귀속
  - `project_attr_defs`·`project_attrs` 테이블 — 도메인별 기준정보 정의·값
- **분류 관리** (`admin.py`, `admin/categories.html`)
  - 카테고리 정의·별칭(`catalog_categories.aliases`) 관리 화면, 기본 표시 순서(`sort_order`) 지정
- **클러스터 표시 순서** (`schema.py`)
  - `catalog_clusters.display_order` 컬럼 추가 — 클러스터 사용자 지정 정렬

### Changed

- **비교 단위 기반 클러스터링 정비** (`catalog_clusterer.py`, `queries.py`)
  - `CLUSTER_PROMPT` 개선 — 이름 우선 매칭, 상위경로·하위구성은 보조 참조, 적극 병합, 레벨 불일치 시에만 분리
  - `list_submission_items_for_clustering`이 각 업체 `compare_units`로 묶어 반환

### Fixed

- **제출서 소프트 삭제** (`schema.py`, `queries.py`)
  - `submissions.deleted_at` 컬럼 추가 — 하드 삭제 대신 소프트 삭제로 비교·집계에서 제외

---

## [0.9.5] — 2026-06 — LLM 연동 · SSL 설정

### Added

- **LLM 제공자 설정** (`schema.py`, `llm_provider.py`)
  - `users.llm_verify_ssl` 컬럼 추가 — 폐쇄망(사내 게이트웨이) 대응 SSL 검증 토글
  - OpenAI 직접 연동 및 사내 게이트웨이 경유 연동 지원

### Changed

- 클러스터링·추출 LLM 호출 경로를 제공자 설정 기반으로 통일

---

## [0.9.4] — 2026-06-25 — 코드 기반 추출 아키텍처

### Added

- **코드 기반 추출 엔진** — 열 매핑 → 결정론적 추출로 LLM path 꼬임 해결
- **인터랙티브 열 매핑 화면** — 열 역할 지정 + 비교 단위 선택
- **업로드 → 열 매핑 추출 흐름** (옛 자동 추출 대체)
- **추출 편집** — subtotal 제외, 매핑 설정 기억, 아이템 삭제
- **Special Nego 수기 입력** — 별도 항목으로 합계·비교 반영, 재추출 시 유지
- **비교 단위(묶음) 기반 클러스터링·비교**
- **같은 이름 항목 경로 구분** (클러스터 멤버 포함)

### Changed

- 공급가액 = 트리 = 라인 합계 일치 보장

### Fixed

- path 구분자 `/` 보존 (분류명 깨짐 방지)
- 추출 엔진과 화면의 path 생성 로직 일치

---

## [0.8.8] — 2026-06-12

### Fixed

- **클러스터링 페이지 500 에러** (`clusters.html`)
  - Jinja2 `{% if %}` 블록 안의 `\"accepted\"` 백슬래시 이스케이프 → 파서 `unexpected char '\'` 오류
  - `\"accepted\"` → `'accepted'`로 교체 (재검토·삭제 confirm 2곳)

---

## [0.8.7] — 2026-06-12

### Fixed

- **클러스터링 "API 키 없음" 오류** (`compare.py`, `catalog.py`)
  - `g.auth_data`가 `before_request`에서 미설정 → `uid = ""` → API 키 조회 실패
  - `auth_data.get("user_id")` 패턴(20여 개)을 `session.get("user_id", "")`로 일괄 교체, `compare.py`에 top-level `session, g` import 추가

### Changed

- **클러스터 선택 UI** (`clusters.html`)
  - "선택" 버튼 → 명시적 체크박스(`accent-blue-600`), 체크 시 파란 링 강조, "전체 선택" 상단 배치

---

## [0.8.6] — 2026-06-12

### Fixed

- **클러스터 삭제/재검토 오류** (`catalog_clusterer.py`)
  - `reopen_cluster`·`delete_cluster`·`reset_bid_clusters`의 `match_status = NULL`이 `NOT NULL` 제약 위반 → `match_status = 'pending'`으로 변경

### Changed

- **클러스터 선택 UI** (`clusters.html`) — 체크박스 → "선택"/"✓ 선택됨" 토글, 전체 선택/해제, 병합 confirm에 대상 이름 표시
- **클러스터 리셋 버튼** (`bid.html`) — 상단 헤더에 클러스터 리셋 노출

---

## [0.8.5] — 2026-06-12

### Added

- **확정 클러스터 수정/삭제** (`catalog_clusterer.py`, `catalog.py`, `compare.py`) — `reopen_cluster`·`delete_cluster`·`reset_bid_clusters` 및 관련 라우트
- **클러스터 단위 선택(병합용)** (`clusters.html`) — 선택 UI·전체 선택·일괄 병합/삭제 액션바
- **클러스터링 리셋** — 클러스터링 페이지·비교 페이지에 입찰 전체 초기화
- **모든 상태 클러스터 액션 노출** — 확정/거부/보류에도 재검토·삭제 버튼

---

## [0.8.1 ~ 0.8.4] — 2026-06-11

### Added

- **클러스터링 엔진** (`catalog_clusterer.py`) — `run_clustering`·`accept_cluster`·`reject_cluster`·`hold_cluster`·`merge_clusters`·`rename_cluster`
- **클러스터 라우트** (`catalog.py`, `compare.py`) — 목록/상세/수락/거부/보류/병합/이름변경
- **템플릿** (`clusters.html`, `cluster_detail.html`) — 클러스터 목록·상세 뷰
- **업체별 비교 페이지** (`bid.html`) — 클러스터 배지·품목 하이라이트·관리 링크
