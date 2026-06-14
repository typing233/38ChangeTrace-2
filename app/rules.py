import re
import logging
from lxml import etree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonitoringRule

logger = logging.getLogger(__name__)


async def evaluate_rules(task_id: int, old_text: str, new_text: str, new_html: str, session: AsyncSession, old_html: str = "") -> bool:
    result = await session.execute(
        select(MonitoringRule).where(
            MonitoringRule.task_id == task_id,
            MonitoringRule.enabled == True
        )
    )
    rules = result.scalars().all()

    if not rules:
        return True

    diff_text = _compute_change_text(old_text, new_text)
    groups: dict[str, list[MonitoringRule]] = {}
    for rule in rules:
        groups.setdefault(rule.logic_group, []).append(rule)

    group_results = []
    for group_name, group_rules in groups.items():
        results = [_evaluate_single_rule(r, old_text, new_text, old_html, new_html, diff_text) for r in group_rules]
        if group_name.upper() == "OR":
            group_results.append(any(results))
        else:
            group_results.append(all(results))

    return all(group_results)


def _compute_change_text(old_text: str, new_text: str) -> str:
    old_lines = set(old_text.splitlines())
    new_lines = new_text.splitlines()
    added = [line for line in new_lines if line not in old_lines]
    removed_set = set(new_text.splitlines())
    removed = [line for line in old_text.splitlines() if line not in removed_set]
    return "\n".join(added + removed)


def _evaluate_single_rule(rule: MonitoringRule, old_text: str, new_text: str, old_html: str, new_html: str, diff_text: str) -> bool:
    try:
        if rule.rule_type == "xpath":
            return _eval_xpath(rule.config, old_html, new_html)
        elif rule.rule_type == "css":
            return _eval_css(rule.config, old_html, new_html)
        elif rule.rule_type == "keyword_include":
            return _eval_keyword_include(rule.config, diff_text)
        elif rule.rule_type == "keyword_exclude":
            return _eval_keyword_exclude(rule.config, diff_text)
        elif rule.rule_type == "regex":
            return _eval_regex(rule.config, diff_text)
        else:
            logger.warning(f"Unknown rule type: {rule.rule_type}")
            return True
    except Exception as e:
        logger.error(f"Rule {rule.id} evaluation failed: {e}")
        return True


def _eval_xpath(config: dict, old_html: str, new_html: str) -> bool:
    xpath_expr = config.get("xpath", "")
    if not xpath_expr:
        return True
    try:
        old_value = ""
        if old_html:
            old_tree = etree.HTML(old_html)
            old_results = old_tree.xpath(xpath_expr)
            old_value = _xpath_results_to_text(old_results)

        new_tree = etree.HTML(new_html)
        new_results = new_tree.xpath(xpath_expr)
        new_value = _xpath_results_to_text(new_results)

        return old_value != new_value
    except Exception as e:
        logger.error(f"XPath evaluation error: {e}")
        return True


def _eval_css(config: dict, old_html: str, new_html: str) -> bool:
    selector = config.get("selector", "")
    if not selector:
        return True
    try:
        from bs4 import BeautifulSoup

        old_value = ""
        if old_html:
            old_soup = BeautifulSoup(old_html, "lxml")
            old_els = old_soup.select(selector)
            old_value = "\n".join(el.get_text(strip=True) for el in old_els)

        new_soup = BeautifulSoup(new_html, "lxml")
        new_els = new_soup.select(selector)
        new_value = "\n".join(el.get_text(strip=True) for el in new_els)

        return old_value != new_value
    except Exception as e:
        logger.error(f"CSS selector evaluation error: {e}")
        return True


def _xpath_results_to_text(results) -> str:
    parts = []
    for r in results:
        if isinstance(r, str):
            parts.append(r)
        elif hasattr(r, "text") and r.text:
            parts.append(r.text)
        elif hasattr(r, "text_content"):
            parts.append(r.text_content())
    return "\n".join(parts)


def _eval_keyword_include(config: dict, diff_text: str) -> bool:
    keywords = config.get("keywords", [])
    if not keywords:
        return True
    if not diff_text.strip():
        return False
    text_lower = diff_text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _eval_keyword_exclude(config: dict, diff_text: str) -> bool:
    keywords = config.get("keywords", [])
    if not keywords:
        return True
    if not diff_text.strip():
        return True
    text_lower = diff_text.lower()
    return not any(kw.lower() in text_lower for kw in keywords)


def _eval_regex(config: dict, diff_text: str) -> bool:
    pattern = config.get("pattern", "")
    if not pattern:
        return True
    if not diff_text.strip():
        return False
    flags_str = config.get("flags", "")
    flags = 0
    if "i" in flags_str:
        flags |= re.IGNORECASE
    if "m" in flags_str:
        flags |= re.MULTILINE
    if "s" in flags_str:
        flags |= re.DOTALL
    return bool(re.search(pattern, diff_text, flags))
