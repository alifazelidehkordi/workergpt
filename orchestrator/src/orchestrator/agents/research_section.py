"""Agents used by the durable, section-by-section research workflow."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


def _bullets(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "- موردی ثبت نشده"
    rendered = []
    for value in values:
        if isinstance(value, dict):
            problem = value.get("problem", "")
            change = value.get("required_change", "")
            strategy = value.get("repair_strategy", "")
            occurrence = value.get("occurrence")
            repeat_note = f" | بار تکرار: {occurrence}" if occurrence else ""
            strategy_note = f" | راهبرد: {strategy}" if strategy else ""
            rendered.append(
                f"- مشکل: {problem} | تغییر لازم: {change}{repeat_note}{strategy_note}"
            )
        else:
            rendered.append(f"- {value}")
    return "\n".join(rendered)


class ResearchTopicPlannerAgent(BaseAgent):
    name = "research_topic_planner"
    description = "Create a bounded six-section research plan"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""فقط یک کار داری: برای پروندهٔ زیر یک برنامهٔ پژوهش علمی شش‌بخشی بساز؛ خود پرونده را ننویس.

شناسه: {context.get('topic_id', '')}
عنوان: {context.get('topic_title', '')}
هدف/زیرموضوع نوشته‌شده توسط کاربر:
{context.get('topic_excerpt', '')}

سیاست پژوهش:
{context.get('research_policy', '')}

دقیقاً یک بلوک JSON fenced برگردان. کلیدها باید دقیقاً این شش شناسه باشند:
01_construct, 02_theories, 03_mechanisms, 04_evidence, 05_limits, 06_practice
ساختار هر مقدار:
{{"questions": ["bounded research question"], "search_queries": ["specific query"], "exclude": ["material to exclude"]}}
هر سه فهرست باید رشته‌های مشخص و غیرخالی داشته باشند. کلید یا نثر اضافه نکن."""


class ResearchSectionAgent(BaseAgent):
    name = "research_section"
    description = "Research one bounded section of a scientific topic"

    def build_prompt(self, context: dict[str, Any]) -> str:
        plan = context.get("section_plan") if isinstance(context.get("section_plan"), dict) else {}
        return f"""فقط یک کار داری: بخش تعیین‌شدهٔ زیر را به فارسی پژوهش و نگارش کن.

شناسهٔ موضوع: {context.get('topic_id', '')}
عنوان موضوع: {context.get('topic_title', '')}
تیتر دقیق بخش: {context.get('section_title', '')}

پرسش‌های همین بخش:
{_bullets(plan.get('questions'))}

عبارت‌های جست‌وجو:
{_bullets(plan.get('search_queries'))}

موارد خارج از دامنه:
{_bullets(plan.get('exclude'))}

بازخورد تلاش قبلی:
{_bullets(context.get('review_feedback'))}

برنامهٔ اصلاح ثبت‌شده:
{context.get('repair_plan') or 'برای این تلاش برنامهٔ تکرارشونده‌ای ثبت نشده است.'}

سیاست پژوهش:
{context.get('research_policy', '')}

الزامات بخش:
{context.get('section_instructions', '')}

جست‌وجوی وب را انجام بده و صفحهٔ هر منبع را باز کن؛ فقط از منبعی استفاده کن که محتوای آن را واقعاً بررسی کرده‌ای. DOI، PMID، حجم نمونه، اندازهٔ اثر یا نتیجهٔ علّی نساز. جزئیات نامطمئن را صریح علامت بزن.

فقط همین بخش را در ۶۰۰ تا ۹۰۰ واژه بنویس و با تیتر دقیق بالا آغاز کن. ادعاها را با [1]، [2] و مانند آن ارجاع بده و در پایان منابع را با URL کامل و واقعی https به DOI، PubMed/PMC یا ناشر رسمی بیاور. نظریه، همبستگی، علیت و استنباط طراحی را جدا نگه دار. contentReference، oaicite، پیام رابط کاربری یا متن ناقص تولید نکن.

در حالت عادی دقیقاً یک بلوک Markdown fenced و هیچ نثر دیگری برگردان. اگر پژوهش قابل‌اعتماد ممکن نیست، فقط این JSON fenced را برگردان: {{"status":"blocked","reason":"دلیل مشخص"}}."""


class ResearchSectionCriticAgent(BaseAgent):
    name = "research_section_critic"
    description = "Audit one scientific research section without rewriting it"

    def build_prompt(self, context: dict[str, Any]) -> str:
        plan = context.get("section_plan") if isinstance(context.get("section_plan"), dict) else {}
        return f"""فقط یک کار داری: این بخش را به‌عنوان ممیز علمی مستقل بررسی کن؛ متن را بازنویسی نکن.

شناسهٔ موضوع: {context.get('topic_id', '')}
عنوان موضوع: {context.get('topic_title', '')}
تیتر دقیق بخش: {context.get('section_title', '')}

پرسش‌هایی که بخش باید پاسخ دهد:
{_bullets(plan.get('questions'))}

موارد خارج از دامنه:
{_bullets(plan.get('exclude'))}

جست‌وجوی وب را انجام بده، هر URL استنادشده را باز کن و تطابق ادعا با منبع را بررسی کن. منابع ساختگی یا نامنطبق، زبان علّی بی‌پشتوانه، حذف شواهد منفی، محدودیت‌های نمونه/روش، توصیهٔ ناایمن، خلط مفهومی و نقص پوشش را ممیزی کن.

متن بخش:
{context.get('section_draft', '')}

دقیقاً یک بلوک JSON fenced و هیچ متن دیگری با این ساختار برگردان:
{{
  "verdict": "pass" or "revise",
  "issues": [{{"id":"stable id","severity":"blocking|major|minor","location":"specific location","excerpt":"exact quote copied from the draft","problem":"what is wrong","required_change":"what the researcher must change"}}],
  "source_checks": [{{"claim":"checked claim","url":"https URL","result":"verified|mismatch|unverifiable","note":"short note"}}]
}}
`blocking` را فقط برای جعل/عدم قابلیت ردیابی منبعِ ادعای محوری، خطر ایمنی، متن ناقص یا پاسخ به بخش اشتباه استفاده کن. `major` پیشنهاد اصلاح مهم ولی غیرمسدودکننده است. excerpt باید عیناً از Draft کپی شود؛ ایراد بدون نشانی دقیق نساز. فقط وقتی issue مسدودکننده یا major وجود ندارد `pass` بده. Markdown اصلاح‌شده تولید نکن."""


class ResearchSectionRepairAgent(BaseAgent):
    name = "research_section_repair"
    description = "Patch only the passages identified by the section critic"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""فقط قسمت‌های مشخص‌شده در برنامهٔ اصلاح را تعمیر کن؛ کل بخش را بازنویسی نکن.

موضوع: {context.get('topic_title', '')}
تیتر ثابت: {context.get('section_title', '')}

برنامهٔ اصلاح مبتنی بر JSON منتقد:
{context.get('repair_plan', '')}

فقط قطعه‌های مرتبطی که ارکسترا از Draft انتخاب کرده است:
<repair-context>
{context.get('repair_context', '')}
</repair-context>

برای هر ایراد فقط از `excerpt` مربوط به همان issue استفاده کن. `old_text` باید عیناً در همان excerpt وجود داشته باشد. `new_text` فقط جایگزین همان قطعه باشد و می‌تواند برای ادعای اصلاح‌شده ارجاع و منبع لازم را اضافه کند. به قسمت‌های خارج از repair-context دسترسی نداری و نباید آن‌ها را بازسازی کنی. اگر ایراد با حذف ادعا حل می‌شود، `new_text` را نسخهٔ محدودشده یا حذف ایمن آن قرار بده.

فقط یک بلوک JSON fenced برگردان:
{{
  "patches": [
    {{"issue_id":"شناسهٔ ایراد","old_text":"نقل دقیق از متن فعلی","new_text":"متن جایگزین موضعی"}}
  ]
}}
هیچ متن کامل Markdown، توضیح تغییرات یا حکم pass تولید نکن."""


class ResearchFileCriticAgent(BaseAgent):
    name = "research_file_critic"
    description = "Final quality gate for a complete scientific dossier"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""فقط یک کار داری: پروندهٔ کامل زیر را به‌عنوان ممیز علمی نهایی بررسی کن؛ آن را بازنویسی نکن.
جست‌وجوی وب را انجام بده و برای بررسی قابلیت ردیابی و تطابق ادعاها، URLهای منابع را باز کن. کامل‌بودن، سازگاری درونی، کالیبراسیون شواهد، شواهد منفی، مرزهای ایمنی، زیاده‌روی قانون‌های عملی، frontmatter، تیترها، calloutها، wikilinkها، جدول‌ها و placeholderها را بررسی کن.

Required checklist:
{context.get('validation_policy', '')}

Complete dossier:
{context.get('assembled_dossier', '')}

دقیقاً یک بلوک JSON fenced با این ساختار و هیچ متن دیگری برگردان:
{{
  "verdict": "pass" or "revise",
  "issues": [{{"id":"stable issue id","section_ids":["01_construct"],"severity":"blocking|major|minor","location":"specific location","excerpt":"exact quote from the affected section","problem":"what is wrong","required_change":"what must change"}}],
  "summary": "short Persian summary"
}}
فقط وقتی issue مسدودکننده یا major باقی نمانده `pass` بده. فقط از شش شناسهٔ شناخته‌شده استفاده کن و پرونده را بازنویسی نکن."""
