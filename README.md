# Safe-Agents

פלטפורמת Desktop להורדה, התקנה והרצה של אייג'נטים מבוססי LangGraph + OpenRouter.

- [מסמך אפיון ותכנון](docs/SPEC.md)
- [חוזה אייג'נט למפתחים](docs/AGENT_CONTRACT.md)
- [JSON Schema למניפסט](schemas/manifest.schema.json) · [JSON Schema לקטלוג](schemas/registry.schema.json)

| תיקייה | תוכן | שלב |
|---|---|---|
| [`manager/`](manager/) | Agent Manager – קטלוג, התקנה והרצה של אייג'נטים (CLI + API מקומי) | M1 ✅ · M2 ✅ |
| [`registry-template/`](registry-template/) | תבנית לריפו הקטלוג: `registry.json` + CI ולידציה | M2 ✅ |
| [`examples/foo-agent/`](examples/foo-agent/) | אייג'נט דוגמה לפי החוזה, כולל HITL | M0 ✅ |
