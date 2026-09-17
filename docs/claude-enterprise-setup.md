# 사내 Claude Enterprise 연동 방법

이 문서는 ProjectDB-Search의 **선택적** 기능인 "애매한 질의에 대한 LLM 재랭킹"을
사내 Claude Enterprise와 연결하는 방법을 설명합니다.

## 이 기능이 하는 일 (그리고 하지 않는 일)

- 규칙 기반 검색 결과의 1위 점수가 낮거나, 1·2위 점수 차이가 작을 때(애매한 경우)에만
  호출됩니다. 명확한 질의에는 전혀 호출되지 않습니다.
- Claude에게 전달되는 것은 **후보 문서들의 메타데이터뿐**입니다 — 파일 경로, 점수,
  어떤 필드가 매칭됐는지. **문서 원문 내용은 절대 전송하지 않습니다.**
- 설정하지 않으면(기본값) 완전히 오프라인으로 동작하며, 이 기능만 비활성화됩니다.
  검색 자체는 항상 정상 동작합니다.

## 설정 방법

`config/default_config.toml`의 `[llm]` 섹션을 수정합니다:

```toml
[llm]
api_key = "sk-ant-..."                          # Claude Enterprise 관리자에게 발급받은 키
base_url = "https://<사내-게이트웨이-주소>/v1"    # 관리자에게 문의
model = "claude-sonnet-4-5"                     # 게이트웨이가 지원하는 모델명으로 조정
```

세 값 모두 **사내 Claude Enterprise 관리자에게 문의해서 받아야 하는 정보**입니다 —
이 프로젝트가 임의로 알 수 없는 값입니다:

| 값 | 무엇을 물어봐야 하는지 |
|---|---|
| `base_url` | "Anthropic Messages API와 호환되는 사내 게이트웨이 엔드포인트 URL이 있나요?" |
| `api_key` | "이 게이트웨이용으로 발급받은 API 키/자격증명" |
| `model` | "게이트웨이에서 어떤 모델명을 사용해야 하나요?" (보통 `claude-sonnet-4-5` 등 표준 모델명 그대로 사용하지만, 사내 게이트웨이가 별도 별칭을 쓸 수도 있음) |

**중요한 전제**: 이 앱은 Anthropic 공식 Python SDK(`anthropic` 패키지)의
`base_url` 오버라이드 기능을 그대로 사용합니다. 즉 사내 게이트웨이가
**Anthropic Messages API와 동일한 요청/응답 형식**을 지원해야 코드 변경 없이
바로 붙습니다. 게이트웨이가 다른 형식(OpenAI 호환 등)이라면 별도 백엔드
구현이 필요하니 먼저 관리자에게 확인하세요.

## 왜 코드 수정이 필요 없는지

`AnthropicAPIBackend`(`src/projectdb_search/search/llm/anthropic_backend.py`)는
처음부터 `api_key`/`base_url`/`model`을 설정 파일에서만 읽도록 설계했습니다.
즉 **개발 단계(공개 Anthropic API)에서 사내 게이트웨이로 전환하는 것은
설정 파일 세 줄을 바꾸는 것으로 끝**나며, 코드를 다시 빌드/배포할 필요가
없습니다.

## 설정이 잘못됐을 때는?

안전합니다. 연결 실패나 응답 파싱 실패는 내부적으로 잡아서 **규칙 기반 검색
결과로 자동 폴백**합니다 — 잘못된 `base_url`을 넣었다고 검색 자체가
죽거나 에러가 나지 않습니다. 다만 이 경우 재랭킹 기능이 조용히 동작하지
않을 뿐이니, 설정 후에는 아래처럼 직접 확인하는 것을 권장합니다.

## 연결 확인 방법

애매한 결과가 나올 만한 질의로 검색해서 `used_llm_rerank` 값을 확인합니다:

```bash
projectdb-search search "totally ambiguous query" --json
```

출력의 `"used_llm_rerank": true`가 뜨면 정상적으로 Claude Enterprise를
거쳐 재랭킹된 것입니다. `false`만 계속 나온다면:
1. `config/default_config.toml`의 `api_key`가 빈 문자열이 아닌지 확인
2. 질의가 애초에 "애매한" 경우가 아닐 수 있음 (명확한 질의는 재랭킹을 타지 않음 — 정상 동작)
3. `base_url`/`api_key`가 실제로 유효한지 관리자와 재확인

## 참고: 임베더블 패키지 배포본에서의 위치

`packaging/embeddable/`로 만든 배포본에서는 이 설정 파일이
`ProjectDB-Search/config/default_config.toml`에 위치합니다. 여러 동료가
같은 배포본을 공유해서 쓴다면, API 키가 평문으로 저장된다는 점을
고려해 배포본 폴더 접근 권한을 적절히 제한하세요.
