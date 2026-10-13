# Session Summary Input

Write this structure to the `input_file` supplied by `begin`. Replace the example content with facts from the current chat. `title` is a short, natural Korean description of the actual work, without a date or project prefix; the recorder adds the project to the filename.

```json
{
  "title": "직원 화면 갱신 오류 분석",
  "summary": "갱신 오류의 후보 원인을 분석했고 실행 검증은 남아 있음",
  "sections": {
    "goal_scope": ["사용자가 요청한 목표와 중요한 제약"],
    "progress": ["실제 완료 사항과 해결하지 못한 사항"],
    "changed_files": ["실제로 변경한 파일과 변경 목적; 변경 없으면 명시"],
    "decisions": ["선택한 방식과 이유, 중요한 제외안"],
    "verification": ["실행한 검사와 결과, 실행하지 않은 검증"],
    "issues": ["남은 오류, 불확실성, 진행을 막는 조건"],
    "next_steps": ["다음 세션에서 할 첫 행동; 남은 일이 없으면 명시"],
    "links": ["확인한 이슈, PR, 커밋, 문서 또는 프로젝트 노트 링크"]
  }
}
```

`progress` and `next_steps` are required. Omit other sections or use empty lists when irrelevant. Do not include invented links, raw transcript dumps, tokens, passwords, authorization headers, or cost metadata. The example is a format illustration, not evidence about the user's project.
