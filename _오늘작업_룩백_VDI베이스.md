# QDBT v0.8.7 — 오늘 작업 룩백 & VDI 베이스

확정일: 2026-06-23
구성: 22일 통합본(qdbt_2026-06-22_2) + 오늘 작업 전체
용도: VDI 운영 베이스 (역방향 업로드 불가 → 이 zip이 정본)

---

## 오늘 작업 룩백 (시간순)

### A. VDI 게이트웨이 LLM 연동
문제: 사내 게이트웨이로 GPT 호출 실패
해결:
- 모델명: gpt-5.1-2025-11-13-sko (API당 1모델 고정 구조)
- 토큰 파라미터: max_tokens ↔ max_completion_tokens 자동 적응
  (하드코딩 없이 API 응답 보고 모델별로 선택)
- base_url/verify_ssl: 모든 LLM 호출 경로에 전달

### B. 모델 자동 조회
프로필에서 "모델 불러오기" → 게이트웨이 /models 조회 → 자동 채움
API당 1모델이면 자동 선택

### C. 클러스터링 게이트웨이 연동
추출은 됐으나 클러스터링만 실패 → base_url이 클러스터링 경로에
전달 안 됨이 원인. 전 경로에 base_url/verify_ssl 전달.
임베딩 없으면 어휘 폴백.

### D. 공유 카탈로그 삭제 버그
입찰2 클러스터 삭제 시 입찰1이 공유하던 카탈로그까지 삭제되던 문제.
_release_cluster_catalog() 헬퍼로 "다른 클러스터가 쓰는지" 확인 후
공유 중이면 보존, 아니면 삭제. (3함수 적용)

### E. 엑셀 파싱 RGB 색상 에러
'RGB' object is not subscriptable — 색상값이 문자열 아닌 객체일 때 발생.
타입 확인 + try/except로 안전 처리.

---

## 변경된 파일 (7개)

| 파일 | 변경 내용 | 관련 작업 |
|------|-----------|-----------|
| src/extractors/providers/gpt.py | 토큰 파라미터 자동 적응 + base_url | A |
| src/extractors/catalog_clusterer.py | 클러스터링 base_url 전달 + 공유카탈로그 삭제버그 | C, D |
| src/web/blueprints/catalog.py | 클러스터링 워커 base_url 전달 | C |
| src/web/blueprints/compare.py | 임베딩 base_url + 어휘 폴백 | C |
| src/web/blueprints/profile.py | 모델 자동조회(/llm/models.json) | B |
| src/web/templates/profile/index.html | 모델 불러오기 UI | B |
| src/parsers/parse_xlsx.py | RGB 색상 에러 안전 처리 | E |

VERSION: 0.8.7-test

---

## 포함 안 된 것 (의도적 제외)

### LLMConfig 리팩토링 — 미적용
"우선 작동" 원칙으로 구조 개선은 제외.
submissions.py는 22일 원본 그대로(LLMConfig 의존 없음).
→ 베이스 안정화 후 별도 적용 예정.

### 시트 선택 기능 (방식 A) — 미구현
다음 작업. 엑셀 탭 목록 읽기 → 탭 선택 → 선택 탭만 LLM 추출.
탭 역할 지정(갑지/을지/상세) + 병합셀 복원 + 대용량 청킹 포함 예정.

### 분류(대/중/소) 클러스터링 — 미구현
탭 선택과 연계. catalog_categories의 parent_id 계층 활용.
을지 병합셀에서 대/중분류 복원이 선행 필요.

### 카탈로그 이름 동기화 버그 — 진단만, 미수정
증상 재현 안 돼 보류. 진단 문서 보관.
(QDBT_카탈로그_이름동기화_진단.md)

---

## 검증 완료

```
✅ 구문 정상 35개 파일
✅ 앱 기동 정상 (라우트 75개)
✅ gpt 토큰 자동적응
✅ 클러스터링 base_url 전달
✅ 공유카탈로그 삭제 헬퍼
✅ 모델 자동조회 라우트
✅ RGB 색상 안전 처리
```

VDI에서 실제 확인된 것:
- gpt-5.1-sko 단일 추출 성공
- 클러스터링 성공
- 시트 많은 엑셀은 멈춤(→ 다음 작업 시트선택으로 해결 예정)

---

## 다음 작업 (이 베이스 위에서)

우선순위:
1. 시트 선택 (방식 A) — 멈춤 문제 해결
   - 1단계: 탭 목록 읽기 + 선택 UI (업로드/파서/LLM 분리)
   - 2단계: 병합셀 복원 + 분류 저장
   - 3단계: 대용량(을지 ~1000행) 청킹
   - 4단계: 분류 수준 클러스터링
2. LLM 관리 모듈 (LLMConfig + LLMClient) — 구조 개선
3. 카탈로그 이름 동기화 버그 — 재현 시 수정
4. 다중 클러스터 일괄 확정 기능

---

## 사용

이 zip이 VDI 운영 정본.
압축 풀면 qdbt_base_v087/ 폴더.
data/ 의 DB·업로드는 제외됨 → 기존 운영 폴더의 data/ 유지하거나 복사.

워크플로: 화면=리플릿, 로직/구조=클로드.
역방향 업로드 불가 → 이 베이스를 기준으로 클로드가 수정본 제공.
