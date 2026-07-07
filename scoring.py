"""AI-based job matching score via the Anthropic API."""

import json
import logging
import os
import re

import anthropic

logger = logging.getLogger("job_hunter.scoring")

SYSTEM_PROMPT = """你是一个招聘匹配助手。根据候选人档案和职位信息，评估匹配程度。
只返回一个 JSON 对象，不要有任何其他文字、解释或 Markdown 代码块标记。
JSON 格式必须严格如下：
{"score": 0到100的整数, "sponsorship_likelihood": "likely 或 unclear 或 unlikely", \
"seniority_fit": "good 或 too_junior 或 too_senior", "one_line_reason": "一句话中文理由"}

打分规则：
- score 反映候选人背景与该职位的匹配程度（教育背景、专业方向、经验、职级）。
- sponsorship_likelihood：根据职位描述判断该公司/职位是否可能为国际候选人提供工作签证担保。
  如果描述中明确提到提供签证担保，为 "likely"；如果没有任何相关信息，为 "unclear"；
  如果有间接迹象表明不太可能（例如强调本地候选人优先、政府/国防相关但未明确要求安全审查等），为 "unlikely"。
  如果提供了"该公司历史签证担保数据"，应作为重要参考：历史上有过多次 H-1B/E-3 担保记录的公司，
  即使职位描述里没提签证的事，也不应该仅因此判为 unlikely；反之历史记录里完全查不到、
  又在描述中有不提供担保暗示的，应更倾向于 unlikely。
- seniority_fit：判断该职位级别是否适合候选人（不含实习生/学生工，不含总监/VP 及以上）。
"""

USER_PROMPT_TEMPLATE = """[候选人档案]
{profile}

[职位信息]
职位名称: {title}
公司: {company}
地点: {location}
{sponsorship_history}
职位描述:
{description}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_VALID_SPONSORSHIP = {"likely", "unclear", "unlikely"}
_VALID_SENIORITY = {"good", "too_junior", "too_senior"}


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.Anthropic(api_key=api_key)


def _parse_response(text: str) -> dict | None:
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    try:
        score = int(data["score"])
    except (KeyError, TypeError, ValueError):
        return None
    score = max(0, min(100, score))

    sponsorship = str(data.get("sponsorship_likelihood", "unclear")).lower()
    if sponsorship not in _VALID_SPONSORSHIP:
        sponsorship = "unclear"

    seniority = str(data.get("seniority_fit", "good")).lower()
    if seniority not in _VALID_SENIORITY:
        seniority = "good"

    reason = str(data.get("one_line_reason", "")).strip()

    return {
        "score": score,
        "sponsorship_likelihood": sponsorship,
        "seniority_fit": seniority,
        "one_line_reason": reason,
    }


def _format_sponsorship_history(history: dict | None) -> str:
    if not history:
        return ""
    case_count = history.get("case_count", 0)
    certified_count = history.get("certified_count", 0)
    rate = f"{certified_count / case_count:.0%}" if case_count else "N/A"
    return (
        f"该公司历史签证担保数据（DOL 公开 LCA/H-1B 披露数据，单个季度快照）："
        f"共 {case_count} 起 H-1B/E-3 相关申请，其中 {certified_count} 起获批（约 {rate}）。\n"
    )


def score_job(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    candidate_profile: str,
    job: dict,
    sponsorship_history: dict | None = None,
) -> dict | None:
    """Return {score, sponsorship_likelihood, seniority_fit, one_line_reason} or None on failure."""
    prompt = USER_PROMPT_TEMPLATE.format(
        profile=candidate_profile.strip(),
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        sponsorship_history=_format_sponsorship_history(sponsorship_history),
        description=(job.get("description") or "")[:6000],
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.exception("Anthropic API call failed for job: %s @ %s", job.get("title"), job.get("company"))
        return None

    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    text = "\n".join(text_parts)
    parsed = _parse_response(text)
    if parsed is None:
        logger.warning("Could not parse AI response for job: %s @ %s -> %r", job.get("title"), job.get("company"), text)
    return parsed
