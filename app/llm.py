from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)


async def analyze_positions(answer_blob_a: str, answer_blob_b: str, language: str = "ru") -> dict:
    output_language = "Russian" if language == "ru" else "English"
    prompt = f"""
You are a neutral conflict-mediation assistant.
Analyze two participants' answers.
Return strict JSON with keys:
summary_a, summary_b, common_ground, differences, options.

Rules:
- Be neutral and concise.
- Do not choose sides.
- Respect privacy markers like [PRIVATE] and do not expose their contents in summaries for the other side.
- options must be an array of 3 short concrete resolution options.
- Write in {output_language}.

Participant A:
{answer_blob_a}

Participant B:
{answer_blob_b}
"""
    response = await client.responses.create(
        model=settings.openai_model,
        input=prompt,
        text={"format": {"type": "json_schema", "name": "mediation_analysis", "schema": {
            "type": "object",
            "properties": {
                "summary_a": {"type": "string"},
                "summary_b": {"type": "string"},
                "common_ground": {"type": "string"},
                "differences": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3
                }
            },
            "required": ["summary_a", "summary_b", "common_ground", "differences", "options"],
            "additionalProperties": False
        }}},
    )
    return response.output_parsed
