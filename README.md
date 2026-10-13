# zerobear OS

Codex·Claude Code용 공통 지침과 스킬을 관리하는 개인 AI 개발 환경.

## 설치

Python 3.9 이상. 저장소 폴더에서 실행.

```sh
python install.py
```

지침 설치 및 스킬 원본 연결. 기존 항목은 백업 후 교체. `python`이 없으면 `python3` 사용.

## 사용

새 대화에서 스킬 이름과 대상 지정. 예: `simple-code로 이 메서드를 리팩토링해줘.`

| 스킬 | 역할 |
| --- | --- |
| `simple-word` | 쉬운 기술 설명 |
| `bug-debug` | 이슈·로그 분석 전용 |
| `code-review` | 코드 리뷰 전용 |
| `simple-code` | 구조·동작을 유지하는 최소 수정 리팩토링 |

스킬 원본 수정은 연결에 반영. 지침 수정·스킬 추가·폴더 이동 시 재설치.

## 제거

```sh
python uninstall.py
```

설치 기록과 일치하는 항목만 제거. 원본·사용자 수정·백업 보존. 백업 자동 복원 없음.
