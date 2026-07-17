from __future__ import annotations

from article_factory.services.flow_schema import (
    FlowDefinition,
    FlowStep,
    FlowStepCompletion,
    FlowStepLoop,
)


def build_standard_sports_flow() -> FlowDefinition:
    writer = FlowStep(
        step_id="00000000-0000-4000-8000-000000000001",
        order=1,
        step_key="writer",
        label="Writer",
        system_prompt="You are a sports journalist. Write clear, engaging articles for a general audience.",
        user_prompt_template=(
            "Topic: {{topic}}\n\n"
            "{{feedback}}"
            "Write a complete article in markdown. If reviewer feedback appears above, revise accordingly."
        ),
    )
    fact_asserter = FlowStep(
        step_id="00000000-0000-4000-8000-000000000002",
        order=2,
        step_key="fact_asserter",
        label="Fact asserter",
        system_prompt="You review a draft for factual accuracy. Flag unsupported or dubious claims.",
        user_prompt_template="Draft:\n{{draft}}\n\nList verified claims and flag anything unsupported or dubious.",
    )
    source_finder = FlowStep(
        step_id="00000000-0000-4000-8000-000000000003",
        order=3,
        step_key="source_finder",
        label="Source finder",
        system_prompt="You find and list credible sources for factual claims in a draft article.",
        user_prompt_template=(
            "Draft:\n{{draft}}\n\nFact check notes:\n{{fact_check}}\n\n"
            "List credible sources with URLs where possible."
        ),
    )
    review = FlowStep(
        step_id="00000000-0000-4000-8000-000000000004",
        order=4,
        step_key="review",
        label="Review",
        system_prompt=(
            "You review the article for quality, accuracy, and readability. "
            "Provide detailed feedback in the body of your response. "
            "End with a final line: VERDICT: ACCEPT or VERDICT: REJECT."
        ),
        user_prompt_template=(
            "Draft:\n{{draft}}\n\nFact check:\n{{fact_check}}\n\nSources:\n{{sources}}\n\n"
            "Review thoroughly, then end with VERDICT: ACCEPT or VERDICT: REJECT."
        ),
        save_response_to_disk=True,
        completion=FlowStepCompletion(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=writer.step_id,
        ),
    )
    return FlowDefinition(
        slug="standard-4-step",
        display_name="Standard 4-step",
        max_iterations=5,
        article_step_id=writer.step_id,
        steps=[writer, fact_asserter, source_finder, review],
    )


def build_single_writer_flow() -> FlowDefinition:
    writer = FlowStep(
        step_id="00000000-0000-4000-8000-000000000101",
        order=1,
        step_key="writer",
        label="Writer",
        system_prompt="You are a journalist. Write clear, engaging articles for a general audience.",
        user_prompt_template="Topic: {{topic}}\n\nWrite a complete article in markdown.",
        completion=FlowStepCompletion(can_complete=True, can_loop=False),
    )
    return FlowDefinition(
        slug="single-writer",
        display_name="Single writer",
        max_iterations=1,
        article_step_id=writer.step_id,
        steps=[writer],
    )


def build_writer_review_flow() -> FlowDefinition:
    writer = FlowStep(
        step_id="00000000-0000-4000-8000-000000000201",
        order=1,
        step_key="writer",
        label="Writer",
        system_prompt="You are a journalist. Write clear, engaging articles for a general audience.",
        user_prompt_template=(
            "Topic: {{topic}}\n\n"
            "{{feedback}}"
            "Write a complete article in markdown. If reviewer feedback appears above, revise accordingly."
        ),
    )
    review = FlowStep(
        step_id="00000000-0000-4000-8000-000000000202",
        order=2,
        step_key="review",
        label="Review",
        system_prompt=(
            "You review the article for quality and readability. "
            "End with a final line: VERDICT: ACCEPT or VERDICT: REJECT."
        ),
        user_prompt_template="Draft:\n{{draft}}\n\nReview thoroughly, then end with VERDICT: ACCEPT or VERDICT: REJECT.",
        save_response_to_disk=True,
        completion=FlowStepCompletion(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=writer.step_id,
        ),
    )
    return FlowDefinition(
        slug="writer-review",
        display_name="Writer + review",
        max_iterations=5,
        article_step_id=writer.step_id,
        steps=[writer, review],
    )


def _beat_flow(
    *,
    slug: str,
    display_name: str,
    beat_brief: str,
    journalist_prompt: str,
) -> FlowDefinition:
    base = build_standard_sports_flow()
    writer = base.steps[0].model_copy(update={"system_prompt": journalist_prompt})
    return base.model_copy(
        update={
            "slug": slug,
            "display_name": display_name,
            "beat_brief": beat_brief,
            "steps": [writer, *base.steps[1:]],
        }
    )


def build_sports_desk_flow() -> FlowDefinition:
    return _beat_flow(
        slug="sports",
        display_name="Sports",
        beat_brief="Games, athletes, leagues, and the stories fans care about.",
        journalist_prompt="You are a sports journalist. Write clear, engaging articles for a general audience.",
    )


def build_business_news_desk_flow() -> FlowDefinition:
    return _beat_flow(
        slug="business-news",
        display_name="Business News",
        beat_brief="Markets, companies, policy, and economic trends for a general business reader.",
        journalist_prompt=(
            "You are a business journalist. Write clear, well-sourced articles about companies, "
            "markets, and the economy for a general audience."
        ),
    )


def build_tech_news_desk_flow() -> FlowDefinition:
    return _beat_flow(
        slug="tech-news",
        display_name="Tech News",
        beat_brief="Products, platforms, security, and industry moves in technology.",
        journalist_prompt=(
            "You are a technology journalist. Explain technical topics clearly for a smart general audience."
        ),
    )


def build_ai_news_desk_flow() -> FlowDefinition:
    return _beat_flow(
        slug="ai-news",
        display_name="AI News",
        beat_brief="Artificial intelligence research, products, policy, and real-world impact.",
        journalist_prompt=(
            "You are an AI beat journalist. Cover models, tools, regulation, and industry impact with clarity and nuance."
        ),
    )
