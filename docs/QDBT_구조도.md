# QDBT 시스템 구조 및 데이터 모델

**조달 견적 비교 도구 (Quote Comparison / Bid Tool)** — Flask · Python · SQLite
전 도메인(IT · 설비 · 용역 등) 입찰 견적서를 업로드·추출·정규화하여 업체별로 원화 기준 비교하는 시스템.

---

## 1. 시스템 아키텍처

계층형 구조로, 라우트(Blueprint) → 도메인 서비스 → 데이터 계층으로 의존성이 단방향으로 흐른다.
모든 SQL은 `db/queries.py`에 집중되어 데이터 접근이 단일 지점으로 통제된다.

```mermaid
flowchart TB
    subgraph client["클라이언트 (브라우저)"]
        UI["Jinja2 템플릿 · Tailwind CSS<br/>비교표 · 열 매핑 · 클러스터 관리"]
    end

    subgraph app["애플리케이션 계층 (Flask)"]
        direction TB
        entry["main.py → create_app()<br/>web/app.py (앱 팩토리 · 필터 등록)"]

        subgraph bp["Blueprints (라우트)"]
            direction LR
            auth_bp["auth<br/>로그인·세션"]
            proj_bp["projects<br/>프로젝트·기준정보"]
            bid_bp["bids<br/>입찰"]
            sub_bp["submissions<br/>업로드·열매핑·추출"]
            cmp_bp["compare<br/>업체 비교·클러스터"]
            cat_bp["catalog<br/>카탈로그·클러스터링"]
            adm_bp["admin<br/>도메인·분류·기준정보정의"]
            prof_bp["profile<br/>LLM 설정"]
        end

        subgraph svc["도메인 서비스"]
            direction TB
            parsers["parsers/<br/>parse_xlsx · parse_pdf · parse_docx"]
            extract["extractors/<br/>extract_by_mapping (코드기반)<br/>llm_extractor (PDF/DOCX)<br/>pipeline (오케스트레이션)"]
            match["extractors/matcher<br/>카탈로그 매칭"]
            cluster["extractors/catalog_clusterer<br/>유사품목 클러스터링 (LLM)"]
            llm["extractors/llm_provider<br/>OpenAI · 사내 게이트웨이"]
        end

        subgraph infra["인프라·보안"]
            direction LR
            authmod["auth/auth<br/>역할 검증 (RBAC)"]
            crypto["auth/crypto<br/>API키 암호화"]
            tok["auth/token_session"]
        end
    end

    subgraph data["데이터 계층"]
        direction TB
        queries["db/queries.py<br/>모든 SQL · 비즈니스 쿼리"]
        schema["db/schema.py<br/>DDL · 마이그레이션"]
        sqlite[("SQLite<br/>15 tables")]
    end

    subgraph ext["외부"]
        openai["OpenAI API"]
        gateway["사내 A.X 게이트웨이<br/>(폐쇄망)"]
    end

    UI <--> bp
    entry --> bp
    bp --> svc
    bp --> infra
    svc --> queries
    infra --> queries
    queries --> schema
    schema --> sqlite
    queries --> sqlite
    llm --> openai
    llm --> gateway

    classDef layer fill:#f8fafc,stroke:#cbd5e1,color:#1e293b
    classDef store fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a
    class sqlite store
```

### 계층 설명

- **애플리케이션 진입점**: `main.py`가 `create_app()`(앱 팩토리)을 호출해 Flask 앱을 구성하고, Jinja 필터(`treepath_above`·`cursym`·`leaf` 등)와 8개 Blueprint를 등록한다.
- **Blueprints (라우트 계층)**: 기능 도메인별로 분리 — 인증(auth), 프로젝트·기준정보(projects), 입찰(bids), 업로드·열매핑·추출(submissions), 업체 비교·클러스터(compare), 카탈로그·클러스터링(catalog), 관리(admin), LLM 설정(profile).
- **도메인 서비스**: 파서(`parsers/`)가 원본 파일을 텍스트/그리드로 변환하고, 추출기(`extractors/`)가 구조화한다. 코드 기반 결정론적 추출(`extract_by_mapping`)과 LLM 추출(`llm_extractor`)이 병존하며, `pipeline`이 오케스트레이션한다. 매칭(`matcher`)·클러스터링(`catalog_clusterer`)이 카탈로그와 정합하고, `llm_provider`가 OpenAI 및 사내 게이트웨이 호출을 추상화한다.
- **인프라·보안**: 역할 기반 접근제어(RBAC, `auth/auth`), API 키 암호화(`crypto`), 토큰 세션(`token_session`).
- **데이터 계층**: `queries.py`(모든 SQL)와 `schema.py`(DDL·마이그레이션)가 SQLite(15 테이블)를 관장한다.

---

## 2. 핵심 데이터 흐름

견적서 업로드부터 비교표 생성까지의 파이프라인. 각 단계가 어떤 함수·테이블과 연결되는지 표시한다.

```mermaid
flowchart LR
    upload["1. 견적서 업로드<br/>(xlsx/pdf/docx)"]
    --> route{"파일 형식"}

    route -->|xlsx| colmap["2a. 열 매핑 화면<br/>suggest_column_mapping<br/>역할 지정 + 비교단위 선택"]
    route -->|pdf/docx| llmx["2b. LLM 추출<br/>llm_extractor"]

    colmap --> extract["3. 결정론적 추출<br/>extract_by_mapping<br/>· 통화 정규화<br/>· 환율 역산 (원화÷통화)<br/>· 원화 확정"]
    llmx --> extract

    extract --> items[("submission_items<br/>잎까지 저장<br/>path=상위, name=잎")]

    items --> recompute["4. 소계 재계산<br/>recompute_subtotal<br/>원화 amount 합산<br/>대표 환율 집계"]

    recompute --> unit["5. 비교단위 묶기<br/>list_submission_items_for_clustering<br/>잎 완전경로(path+name) ↔<br/>compare_units 매칭"]

    unit --> clusterin["6. 클러스터링 입력 구성<br/>_build_cluster_input"]

    clusterin --> llmcluster["7. LLM 클러스터링<br/>catalog_clusterer<br/>영↔한·별칭·수식어 매칭"]

    llmcluster --> clusters[("catalog_clusters<br/>+ members")]

    clusters --> review["8. 사용자 검토<br/>확정·보류·거부·병합<br/>배지 인라인 분류이동"]

    review --> compare["9. 업체별 비교표<br/>compare_bid_submissions<br/>· 원화기준 통일 비교<br/>· 분류순서·금액정렬<br/>· 트리 품명위까지 통일"]

    compare --> hist[("price_history<br/>확정 시 이력 적재")]

    classDef store fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a
    classDef proc fill:#f0fdf4,stroke:#16a34a,color:#14532d
    class items,clusters,hist store
    class extract,recompute,unit,clusterin,compare proc
```

### 흐름 요점

1. **업로드·형식 분기**: xlsx는 결정론적 열 매핑 경로, pdf/docx는 LLM 추출 경로.
2. **추출**: 통화 열을 정규화하고 원화 금액이 함께 있으면 환율을 역산(`원화 ÷ 통화`)하여 원화를 확정한다. 결과는 잎까지 `submission_items`에 저장되며, `path`는 상위 경로만, 잎 이름은 `name_normalized`에 담긴다.
3. **소계·환율 집계**: `recompute_subtotal`이 원화 `amount`를 합산하고 제출서 대표 환율을 집계한다.
4. **비교단위 묶기**: 잎의 완전 경로(`path + 잎 이름`)를 사용자가 지정한 `compare_units`와 대조해, 지정 레벨(잎/중분류)대로 항목을 구성한다. 트리 깊이가 다른 업체 간에도 레벨이 정합한다.
5. **클러스터링**: LLM이 영↔한·별칭·수식어 차이를 넘어 동일 품목을 묶는다.
6. **검토·비교**: 사용자가 클러스터를 확정·병합하고, 비교표는 원화 기준으로 통일 비교하며 확정 시 `price_history`에 이력을 적재한다.

---

## 3. 데이터 모델 (ERD)

15개 테이블. 사용자·도메인을 정점으로, 프로젝트 → 입찰 → 제출서 → 품목의 계층과,
카탈로그(품목 원장) → 클러스터의 정규화 축이 교차한다.

```mermaid
erDiagram
    users ||--o{ projects : "owns (owner_id)"
    users ||--o{ bids : "creates (created_by)"
    users ||--o{ submissions : "uploads/reviews"
    users ||--o{ catalog_items : "creates"
    users ||--o{ catalog_clusters : "reviews"
    users ||--o{ catalog_suggestions : "reviews"
    users ||--o{ bid_watchlist : "adds"

    domains ||..o{ projects : "classifies (domain)"
    domains ||..o{ bids : "classifies (domain)"

    projects ||--o{ bids : "contains"
    projects ||--o{ project_attrs : "has values"
    project_attr_defs ||--o{ project_attrs : "defines"

    bids ||--o{ submissions : "receives"
    bids ||--o{ catalog_clusters : "groups within"
    bids ||--o{ bid_watchlist : "watched in"

    submissions ||--o{ submission_items : "extracted into"
    submissions ||--o{ catalog_suggestions : "proposes"
    submissions ||--o{ price_history : "records"

    submission_items ||--o| catalog_items : "matched to (catalog_item_id)"
    submission_items ||--o{ catalog_suggestions : "suggested from"
    submission_items ||--o{ price_history : "snapshot of"

    catalog_categories ||--o{ catalog_categories : "parent_id (self)"
    catalog_categories ||--o{ catalog_items : "categorizes"

    catalog_items ||--o{ catalog_cluster_members : "member of"
    catalog_items ||--o{ price_history : "priced in"
    catalog_items ||--o{ bid_watchlist : "watched"
    catalog_clusters ||--o{ catalog_cluster_members : "contains"

    users {
        TEXT user_id PK
        TEXT email
        TEXT name
        TEXT role "admin|manager|viewer"
        TEXT password_hash
        TEXT llm_provider "openai|gateway"
        TEXT llm_model
        TEXT llm_api_key_enc "encrypted"
        TEXT llm_base_url
        INTEGER llm_verify_ssl "SSL toggle (closed net)"
        INTEGER is_active
    }

    domains {
        TEXT domain_id PK
        TEXT name "IT|설비|용역|..."
        INTEGER is_active
        INTEGER sort_order
    }

    projects {
        TEXT project_id PK
        TEXT name
        TEXT owner_id FK
        TEXT domain "project domain"
        TEXT status
    }

    project_attr_defs {
        TEXT attr_key PK
        TEXT label
        TEXT value_type
        TEXT unit
        TEXT domain "공통|domain-specific"
        INTEGER sort_order
        INTEGER is_active
    }

    project_attrs {
        TEXT project_id PK,FK
        TEXT attr_key PK,FK
        TEXT value_text
        REAL value_num
    }

    bids {
        TEXT bid_id PK
        TEXT project_id FK
        TEXT name
        DATE due_date
        TEXT domain "bid domain"
        TEXT status
        TEXT category_order "JSON: per-bid category order"
        TEXT created_by FK
    }

    submissions {
        TEXT submission_id PK
        TEXT bid_id FK
        TEXT vendor_name
        TEXT file_name
        TEXT file_path
        TEXT file_format "xlsx|pdf|docx"
        TEXT currency
        INTEGER has_usd_items
        REAL fx_rate_used "representative FX"
        REAL subtotal_excl_vat
        TEXT extraction_status
        INTEGER compare_level
        TEXT compare_units "JSON: user-chosen unit paths"
        TEXT map_config "JSON: column mapping"
        TEXT uploaded_by FK
        TEXT reviewed_by FK
        TIMESTAMP deleted_at "soft delete"
    }

    submission_items {
        TEXT item_id PK
        TEXT submission_id FK
        TEXT line_no
        INTEGER sort_order
        INTEGER depth
        INTEGER is_header
        INTEGER is_nego "special nego"
        TEXT category
        TEXT path "parent path (no leaf)"
        TEXT name_raw
        TEXT name_normalized "leaf name"
        TEXT spec
        REAL quantity
        TEXT unit
        REAL unit_price "KRW"
        REAL unit_price_orig "source currency"
        TEXT unit_price_currency
        REAL fx_rate_used "per-item FX"
        REAL amount "KRW"
        TEXT catalog_item_id FK
        TEXT match_status
    }

    catalog_categories {
        TEXT category_id PK
        TEXT name
        TEXT domain
        TEXT parent_id FK "self-ref"
        INTEGER sort_order
        TEXT aliases "JSON"
        INTEGER is_active
    }

    catalog_items {
        TEXT catalog_item_id PK
        TEXT category_id FK
        TEXT name_canonical
        TEXT aliases "JSON: 영/한/약어"
        TEXT canonical_path
        INTEGER depth
        TEXT observed_paths "JSON"
        INTEGER is_active
        TEXT created_by FK
    }

    catalog_clusters {
        TEXT cluster_id PK
        TEXT bid_id
        TEXT representative_item_id
        TEXT representative_name
        TEXT compare_unit_path
        INTEGER compare_level
        TEXT status "pending|accepted|held|rejected"
        TEXT similarity_summary
        INTEGER display_order
        TEXT reviewed_by FK
    }

    catalog_cluster_members {
        TEXT cluster_id PK,FK
        TEXT catalog_item_id PK
        TEXT role "representative|member"
        REAL similarity_score
    }

    catalog_suggestions {
        TEXT suggestion_id PK
        TEXT submission_id FK
        TEXT item_id FK
        TEXT suggestion_type
        TEXT suggested_name
        TEXT matched_catalog_item_id
        REAL similarity_score
        TEXT status
        TEXT reviewed_by FK
    }

    price_history {
        TEXT record_id PK
        TEXT catalog_item_id FK
        TEXT submission_id FK
        TEXT item_id FK
        TEXT vendor_name
        DATE bid_date
        REAL unit_price
        REAL amount
        TEXT item_path
        TEXT compare_unit
    }

    bid_watchlist {
        TEXT watchlist_id PK
        TEXT bid_id FK
        TEXT catalog_item_id
        TEXT note
        TEXT added_by FK
    }
```

### 테이블 그룹

**운영 축 (입찰 데이터)**
- `projects` → `bids` → `submissions` → `submission_items`: 프로젝트 아래 입찰, 입찰마다 업체 제출서, 제출서에서 추출된 품목. 견적 데이터의 본류.
- `project_attr_defs` / `project_attrs`: 도메인별 기준정보의 정의와 값. `'공통'` 정의는 전 도메인 공유.

**정규화 축 (카탈로그)**
- `catalog_categories`(자기참조 계층) → `catalog_items`: 품목 분류와 표준 품목 원장(별칭·경로 포함).
- `catalog_clusters` / `catalog_cluster_members`: 입찰 내 유사 품목 그룹과 그 멤버.
- `catalog_suggestions`: 추출 품목의 카탈로그 매칭·신규 제안.

**부가 축**
- `price_history`: 확정 시점의 가격 이력(과거 대비 분석용).
- `bid_watchlist`: 입찰별 관심 품목.
- `domains`: 도메인 마스터(IT·설비·용역 등).
- `users`: 계정·역할(RBAC)·LLM 개인 설정(암호화 키·SSL 토글).

### 통화·비교 관련 핵심 컬럼

- `submissions.compare_units` (JSON): 사용자가 지정한 비교 단위 경로 집합. 클러스터링·비교의 레벨을 결정.
- `submissions.fx_rate_used` / `submission_items.fx_rate_used`: 제출서 대표 환율 / 항목별 적용 환율.
- `submission_items.unit_price`(원화) · `unit_price_orig`(원 통화) · `unit_price_currency`: 원화 확정값과 원본 통화 값을 함께 보존.
- `bids.category_order` (JSON): 입찰별 분류 표시 순서.
