"""AI 评审：RAG 召回本项目的评分标准 → 大模型打分 → 写回 review_record。

完整链路：

    提交记录 → 轮次 → 实训记录 → 项目
    项目 → project_file(SCORING_CRITERIA) → file_asset → knowledge_doc(EVAL_CRITERIA)
    学生各关卡作答拼成 query → Milvus 召回与之相关的评分标准片段
    → 拼 prompt → LangChain ChatOpenAI（DeepSeek，OpenAI 兼容端点）→ 结构化 JSON
    → review_record(review_kind=AI, status=FINAL) → finalize_review 结算（分数 / 项目完成 / 技能进度）

两个关键设计：

1. **query 用学生作答，不用固定问题。** 评分场景的检索目的不是"从海量文档里找标准"，
   而是在一份长标准里定位"跟这份作答当前维度对得上的条款"。拿作答当 query 才召得准。
2. **评分标准只在服务端内部检索。** 学生端没有任何入口能调到 ``doc_type=EVAL_CRITERIA``；
   这里传的是显式的 doc_id 白名单，检索阶段用标量过滤，不依赖"检索后再筛"。

评审结果一律不信任模型给的 PASS/FAIL：结论由后端按 ``growth_rule.pass_score`` 判定
（复用 ``attempt.resolve_conclusion``），模型只负责给分和理由。
"""

import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.time import now
from app.crud.attempt import (
    AttemptStageFileRepository,
    AttemptStageRepository,
    FileAssetRepository,
    ProjectSubmissionRepository,
    StudentProjectRepository,
    TrainingAttemptRepository,
)
from app.crud.knowledge import KnowledgeDocRepository
from app.crud.project import ProjectModuleRepository, StageTemplateRepository
from app.crud.review import ReviewAiJobRepository, ReviewRecordRepository
from app.models.enums import KnowledgeDocStatus, KnowledgeDocType
from app.models.knowledge import KnowledgeDoc
from app.models.review import ReviewAiJob
from app.services import parsing, retrieval, settings_store, storage
from app.services.attempt import finalize_review, resolve_conclusion

#: 缺 langchain 依赖时的统一提示
LLM_EXTRA_HINT = "缺少大模型调用依赖（langchain-openai），先执行：uv sync --extra rag"

#: 单段作答与整份作答文档的截断长度，避免把上下文撑爆
MAX_ANSWER_CHARS_PER_STAGE = 4000
MAX_ANSWER_CHARS_TOTAL = 20000

#: 附件：单个附件提取的正文上限、全部附件合计上限、超过这个体积就不解析（防止撑爆内存）
MAX_ATTACHMENT_CHARS = 8000
MAX_ATTACHMENT_CHARS_TOTAL = 20000
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

SYSTEM_PROMPT = """你是一名严谨的实训报告评审专家，负责依据给定的《评分标准》给学生的作答打分。

要求：
1. 严格依据下面提供的评分标准片段打分，不要引入标准之外的个人偏好；
2. **每个维度的分数是"该维度下的得分"，满分就是该维度的权重分**
   （例如权重 30 分的维度，得分在 0~30 之间；不要换算成百分制）；
3. 每个维度给一句可核对的理由，理由里要引用标准中的具体条款；
4. 总分 = 各维度得分之和（权重合计是 100 分制时即为百分制总分）；
5. 标准片段没有覆盖到的内容，按"无法判断"处理并在理由里说明，不要臆造标准；
6. 作答里可能带【附件：文件名】段落，那是学生上传的报告/代码/数据文件，**要一并作为评审依据**；
   标注"无法解析"的附件表示内容未知，按"无法判断"处理，**不要当成学生没交**；
7. **必须逐个维度对照评分标准**：评分标准是按关卡分节写的，每个关卡都要找到自己的那一节。
   确实找不到某关卡条款时，该维度得分记 0 并在理由里写明"评分标准未覆盖"——
   **绝不能因为"没找到扣分依据"就给满分**，那会让评审失去意义；
8. 只输出一个 JSON 对象，不要输出任何解释性文字或 Markdown 代码块。

JSON 结构：
{
  "total_score": 85.5,                      // 总分，各维度得分之和，0~100
  "dimensions": [
    {"name": "需求分析", "score": 17.0, "weight": 20, "reason": "……"}
  ],
  "comment": "整体评语，指出主要扣分点与改进方向"
}"""


@dataclass
class StageAnswer:
    """一个关卡的作答（含该关卡下上传的附件）。"""

    stage_no: int
    name: str
    weight: Decimal
    requirement: str | None
    answer: str | None
    is_filled: bool
    attachments: list["StageAttachment"] = field(default_factory=list)


@dataclass
class StageAttachment:
    """关卡附件：读懂的把正文带上，读不懂的也要让模型知道"有这么个附件"。"""

    file_asset_id: int
    name: str
    content_type: str | None
    size_bytes: int
    text: str | None = None
    error: str | None = None

    @property
    def readable(self) -> bool:
        return self.text is not None

    def render(self) -> str:
        """拼进 prompt 的样式：能读就给正文，不能读也要显式说明，避免模型当成「没交附件」。"""
        if self.readable:
            return f"【附件：{self.name}】\n{self.text}"
        return f"【附件：{self.name}】无法解析（{self.error}），评审时请按「该附件内容未知」处理"


@dataclass
class SubmissionContext:
    """评审所需的一次提交的上下文。"""

    submission_id: int
    attempt_id: int
    student_id: int
    project_id: int
    project_name: str
    answers: list[StageAnswer] = field(default_factory=list)

    @property
    def answer_document(self) -> str:
        """把各关卡作答拼成一份文档，作为检索 query 与 prompt 正文。"""
        parts: list[str] = []
        total = 0
        for item in self.answers:
            body = (item.answer or "").strip() or "（未作答）"
            body = body[:MAX_ANSWER_CHARS_PER_STAGE]
            block = f"## {item.name}（权重 {item.weight}）\n{body}"
            if item.attachments:
                rendered = _render_attachments(item.attachments)
                block = f"{block}\n\n{rendered}"
            total += len(block)
            if total > MAX_ANSWER_CHARS_TOTAL:
                parts.append("……（后续作答因超长省略）")
                break
            parts.append(block)
        return "\n\n".join(parts)


def _render_attachments(attachments: list[StageAttachment]) -> str:
    """渲染某个关卡的附件，整体受 ``MAX_ATTACHMENT_CHARS_TOTAL`` 约束。"""
    blocks: list[str] = []
    used = 0
    for attachment in attachments:
        block = attachment.render()
        remaining = MAX_ATTACHMENT_CHARS_TOTAL - used
        if remaining <= 0:
            blocks.append("……（附件过多，后续附件未纳入评审）")
            break
        if len(block) > remaining:
            block = f"{block[:remaining]}\n……（附件正文超长已截断）"
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def attachment_summary(context: SubmissionContext) -> list[dict[str, Any]]:
    """附件清单台账：记下这次评审到底看没看附件、哪个读不出来。

    完整 prompt 不落库（太长），但"附件有没有喂给模型"必须可追溯——
    否则事后没法解释"为什么这份带了报告的作答被按缺项扣了分"。
    """
    return [
        {
            "file_asset_id": attachment.file_asset_id,
            "name": attachment.name,
            "readable": attachment.readable,
            "chars": len(attachment.text or ""),
            "error": attachment.error,
        }
        for answer in context.answers
        for attachment in answer.attachments
    ]


@dataclass
class AiReviewOutcome:
    """AI 评审结果。"""

    review_id: int
    submission_id: int
    total_score: Decimal
    conclusion: str
    criteria_doc_ids: list[int]
    recalled_chunks: int
    model: str
    warnings: list[str] = field(default_factory=list)


def llm_available() -> bool:
    """langchain-openai 是否可用（没装不影响其它功能，只是不能跑 AI 评审）。"""
    try:
        import langchain_openai  # noqa: F401
    except Exception:
        return False
    return True


# ------------------------------------------------------------------ 上下文装载


async def collect_submission_context(session: AsyncSession, submission_id: int) -> SubmissionContext:
    """装载提交 → 项目 → 各关卡作答。"""
    submissions = ProjectSubmissionRepository(session)
    attempts = TrainingAttemptRepository(session)
    records = StudentProjectRepository(session)
    modules = ProjectModuleRepository(session)
    stages = AttemptStageRepository(session)
    templates = StageTemplateRepository(session)

    submission = await submissions.get(submission_id)
    if submission is None:
        raise NotFoundError(f"提交记录 {submission_id} 不存在")
    if submission.status == "WITHDRAWN":
        raise ConflictError("该提交已被学生撤回，不能评审")

    attempt = await attempts.get(submission.attempt_id)
    if attempt is None:
        raise NotFoundError("提交对应的闯关轮次不存在")
    record = await records.get(attempt.student_project_id)
    if record is None:
        raise NotFoundError("提交对应的实训记录不存在")

    from app.crud.project import TrainingProjectRepository

    project = await TrainingProjectRepository(session).get(record.project_id)
    if project is None:
        raise NotFoundError(f"实训项目 {record.project_id} 不存在")

    context = SubmissionContext(
        submission_id=submission_id,
        attempt_id=attempt.id or 0,
        student_id=record.student_id,
        project_id=record.project_id,
        project_name=project.project_name,
    )
    stage_rows = {row.project_module_id: row for row in await stages.list_of_attempt(context.attempt_id)}
    for module in await modules.list_of_project(context.project_id):
        template = await templates.get(module.template_id)
        row = stage_rows.get(module.id or 0)
        context.answers.append(
            StageAnswer(
                stage_no=module.stage_no,
                name=template.stage_name if template else f"关卡{module.stage_no}",
                weight=module.weight,
                requirement=module.requirement or (template.default_requirement if template else None),
                answer=row.answer_text if row else None,
                is_filled=bool(row and row.is_filled),
                attachments=await load_stage_attachments(session, row.id or 0) if row else [],
            )
        )
    context.answers.sort(key=lambda item: item.stage_no)
    return context


async def load_criteria_docs(session: AsyncSession, project_id: int) -> list[KnowledgeDoc]:
    """取该项目的评分标准文档：project_file → file_asset → knowledge_doc(EVAL_CRITERIA)。"""
    from sqlmodel import select

    from app.models.project import ProjectFile

    asset_ids = [
        int(item)
        for item in (
            await session.exec(select(ProjectFile.file_asset_id).where(ProjectFile.project_id == project_id))
        ).all()
    ]
    if not asset_ids:
        return []
    docs = await KnowledgeDocRepository(session).list_by_file_assets(asset_ids)
    return [doc for doc in docs if doc.doc_type == KnowledgeDocType.EVAL_CRITERIA]


async def load_stage_attachments(session: AsyncSession, stage_id: int) -> list[StageAttachment]:
    """读某个关卡上传的附件并抽取正文。

    学生交的可能是报告 PDF、代码、数据集、截图。复用的是知识入库那套解析器
    （md/txt/csv/docx/pdf/xlsx），所以能读的格式与知识库一致。

    **读不出来也要带上**：附件读不到时留一条 ``error``，渲染时明确写"该附件内容未知"。
    直接跳过会让模型以为学生没交附件，按"缺项"扣分——那是误判。
    """
    links = await AttemptStageFileRepository(session).list_of_stage(stage_id)
    if not links:
        return []

    assets = FileAssetRepository(session)
    attachments: list[StageAttachment] = []
    for link in links:
        asset = await assets.get(link.file_asset_id)
        if asset is None:
            attachments.append(
                StageAttachment(
                    file_asset_id=link.file_asset_id,
                    name=f"#{link.file_asset_id}",
                    content_type=None,
                    size_bytes=0,
                    error="文件台账里找不到这条记录",
                )
            )
            continue

        item = StageAttachment(
            file_asset_id=asset.id or 0,
            name=asset.original_name,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
        )
        if asset.size_bytes > MAX_ATTACHMENT_BYTES:
            limit_mb = MAX_ATTACHMENT_BYTES // 1024 // 1024
            item.error = f"文件 {asset.size_bytes // 1024 // 1024}MB 超过 {limit_mb}MB，未纳入评审"
            attachments.append(item)
            continue
        try:
            content = storage.read_bytes(asset.bucket, asset.object_key)
            parsed = parsing.parse(content, filename=asset.original_name)
            if parsed.is_empty:
                item.error = "没有可提取的文本（扫描件/图片需要先做 OCR）"
            else:
                text = "\n".join(block.text for block in parsed.blocks).strip()
                item.text = text[:MAX_ATTACHMENT_CHARS]
        except Exception as exc:  # 附件读不了不该让整次评审失败
            item.error = str(exc)[:120]
        attachments.append(item)
    return attachments


def _criteria_item(chunk: Any, score: float | None = None) -> dict[str, Any]:
    return {
        "doc_id": chunk.doc_id,
        "chunk_id": chunk.id,
        "heading_path": chunk.heading_path,
        "content": chunk.content,
        "score": score,
    }


async def retrieve_criteria(
    session: AsyncSession, *, context: SubmissionContext, docs: list[KnowledgeDoc]
) -> tuple[list[dict[str, Any]], list[str]]:
    """挑出喂给模型的评分标准片段；返回 ``(片段列表, 提示列表)``。

    评分标准是**不定长**的：切片条数由标题个数决定，不由总字数决定——
    七个模块各写一节是 8 片，每节再拆 `### 细则` 就能到三四十片。
    所以这里不按条数截断，而是分两段处理：

    1. **总字数在预算内** → 整份交给模型（绝大多数教师写的标准都落在这里）；
    2. **超出预算** → 按关卡分维度挑依据，保证**每个维度都有自己的条款**：
       先按标题匹配（标题里含关卡名就确定性归位，不需要向量检索），
       标题对不上的关卡再用该关卡的作答做语义召回，
       还有预算就用整份作答召回补齐，最后按文档顺序填满。

    这样做的原因：整份作答当 query 只取 top-N 时，向量相似度天然偏向"措辞像"的章节，
    措辞差异大的维度会被整条挤掉，模型找不到依据就只能凭感觉打分。
    """
    from app.crud.knowledge import KnowledgeChunkRepository

    doc_ids = [doc.id or 0 for doc in docs if doc.status != KnowledgeDocStatus.DISABLED]
    if not doc_ids:
        return [], ["评分标准文档都已停用，没有可用的评审依据"]

    warnings: list[str] = []
    repository = KnowledgeChunkRepository(session)
    every_chunk = [chunk for doc_id in doc_ids for chunk in await repository.list_of_doc(doc_id)]
    if not every_chunk:
        return [], ["评分标准还没有切片内容，请先确认文档已解析入库"]

    config = await settings_store.get_retrieval_config(session)
    budget = max(1, config.criteria_max_chars)
    total_chars = sum(len(chunk.content) for chunk in every_chunk)
    if total_chars <= budget:
        return [_criteria_item(chunk) for chunk in every_chunk], warnings

    warnings.append(f"评分标准共 {total_chars} 字，超出 {budget} 字预算，已按关卡分维度挑选依据")
    picked: dict[int, dict[str, Any]] = {}
    used = 0

    def take(chunk: Any, score: float | None = None) -> bool:
        nonlocal used
        if chunk.id in picked:
            return False
        if used + len(chunk.content) > budget:
            return False
        picked[chunk.id] = _criteria_item(chunk, score)
        used += len(chunk.content)
        return True

    # 1) 标题里含关卡名 → 确定性归位，不依赖相似度
    buckets: list[tuple[StageAnswer, list[Any]]] = []
    unmatched: list[StageAnswer] = []
    for answer in context.answers:
        name = (answer.name or "").strip()
        matched = [chunk for chunk in every_chunk if name and name in (chunk.heading_path or "")]
        if matched:
            buckets.append((answer, matched))
        else:
            unmatched.append(answer)

    # 2) 轮转取片：先给每个维度保底一片，再一起补第二片……
    #    不能"按维度顺序各取 N 片"——排在后面的维度会被前面的把预算吃光，
    #    实测就是这样丢掉"模型测试""实训报告"两节的。
    covered: set[int] = set()
    for answer, matched in buckets:
        if take(matched[0]):
            covered.add(id(answer))
    round_index = 1
    while True:
        progressed = False
        for _answer, matched in buckets:
            if round_index < len(matched) and take(matched[round_index]):
                progressed = True
        if not progressed:
            break
        round_index += 1

    starved = [answer.name for answer, _matched in buckets if id(answer) not in covered]
    if starved:
        warnings.append(
            f"评分标准超过 {budget} 字预算，这些关卡没能带上专属条款：{'、'.join(starved)}；"
            "建议调大 rag.retrieval.criteria_max_chars 或拆分评分标准"
        )

    # 3) 标题对不上的关卡 → 用该关卡自己的作答做语义召回
    if unmatched:
        try:
            for answer in unmatched:
                result = await retrieval.recall(
                    session,
                    query=(answer.answer or answer.name),
                    doc_ids=doc_ids,
                    doc_type=KnowledgeDocType.EVAL_CRITERIA,
                    top_k=config.criteria_top_k_per_dimension,
                    use_rerank=False,  # 这一步只为"这一维度有依据"，精排交给模型自己判断
                )
                for hit in result.hits:
                    chunk = next((item for item in every_chunk if item.id == hit.chunk_id), None)
                    if chunk is not None:
                        take(chunk, hit.score)
        except Exception as exc:  # 向量链路不可用 → 退到下面的顺序补齐
            warnings.append(f"按关卡语义召回不可用（{exc}），已按文档顺序补齐依据")

    # 3) 还有预算 → 用整份作答召回补齐
    if used < budget:
        try:
            result = await retrieval.recall(
                session,
                query=context.answer_document,
                doc_ids=doc_ids,
                doc_type=KnowledgeDocType.EVAL_CRITERIA,
                use_rerank=False,
            )
            for hit in result.hits:
                chunk = next((item for item in every_chunk if item.id == hit.chunk_id), None)
                if chunk is not None:
                    take(chunk, hit.score)
        except Exception as exc:
            warnings.append(f"整份召回不可用（{exc}）")

    # 4) 仍有余量 → 按文档顺序填满预算
    for chunk in every_chunk:
        if used >= budget:
            break
        take(chunk)

    return sorted(picked.values(), key=lambda item: int(item["chunk_id"] or 0)), warnings


# ------------------------------------------------------------------ 大模型调用


def _build_user_prompt(context: SubmissionContext, criteria: list[dict[str, Any]]) -> str:
    criteria_text = "\n\n".join(
        f"[标准{index}] {item.get('heading_path') or ''}\n{item['content']}"
        for index, item in enumerate(criteria, start=1)
    )
    return (
        f"# 实训项目\n{context.project_name}\n\n"
        f"# 评分标准片段\n{criteria_text}\n\n"
        f"# 学生作答（含各关卡上传的附件）\n{context.answer_document}\n\n"
        "请依据上面的评分标准给这份作答打分，只输出规定的 JSON。"
    )


async def call_llm(
    config: settings_store.LLMConfig, *, system_prompt: str, user_prompt: str
) -> tuple[str, str | None]:
    """调主模型；返回 ``(正文, 请求ID)``。"""
    if not llm_available():
        raise BusinessRuleError(LLM_EXTRA_HINT)
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        timeout=config.timeout,
        max_tokens=config.max_tokens,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
    content = response.content
    if not isinstance(content, str):  # 少数模型返回内容块列表
        content = "".join(str(part) for part in content)
    request_id = getattr(response, "id", None)
    return content, str(request_id) if request_id else None


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def parse_llm_json(text: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON：容忍 ```json 代码块与前后多余文字。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except ValueError:
        matched = _JSON_BLOCK_RE.search(cleaned)
        if matched is None:
            raise BusinessRuleError(f"模型没有返回可解析的 JSON：{text[:200]}") from None
        try:
            payload = json.loads(matched.group(0))
        except ValueError as exc:
            raise BusinessRuleError(f"模型返回的 JSON 解析失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise BusinessRuleError("模型返回的 JSON 顶层不是对象")
    return payload


def _score(value: Any, *, upper: Decimal = Decimal(100)) -> Decimal:
    """分数归一化：夹到 [0, upper]，保留两位小数。"""
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise BusinessRuleError(f"模型给的分数不是数字：{value!r}") from exc
    number = max(Decimal(0), min(upper, number))
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_dimensions(payload: dict[str, Any], context: SubmissionContext) -> list[dict[str, Any]]:
    """把模型给的维度整理成 ``review_record.dimension_json`` 结构。

    **维度分是"该维度下的得分"，满分等于该维度权重**（标准文件本来就是按
    "需求分析 20 分 / 方案设计 30 分"写的，让模型直接按这个口径给分最不容易错）。
    权重以项目模块配置为准——模型自己报的权重只在项目里找不到同名维度时才采信。
    """
    raw_items = payload.get("dimensions")
    if not isinstance(raw_items, list):
        raw_items = []
    weights = {item.name: item.weight for item in context.answers}
    dimensions: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip() or "未命名维度"
        project_weight = weights.get(name)
        if project_weight is not None:
            weight = _score(project_weight)
        elif item.get("weight") is not None:
            weight = _score(item.get("weight"))
        else:
            weight = _score(0)
        # 权重未知时按百分制兜底，避免把分数错误地夹到 0
        upper = weight if weight > 0 else Decimal(100)
        dimensions.append(
            {
                "name": name,
                # 维度分不能超过该维度权重
                "score": str(_score(item.get("score"), upper=upper)),
                "weight": str(weight),
                "reason": (str(item.get("reason")).strip() if item.get("reason") else None),
            }
        )
    return dimensions


def total_score_of(payload: dict[str, Any], dimensions: list[dict[str, Any]]) -> Decimal:
    """总分：模型给了就用模型的（夹到 0~100）；没给就按"各维度得分之和"算。

    这个口径与 SYSTEM_PROMPT 里对模型的约定完全一致（维度满分 = 该维度权重，
    权重合计 100），所以不额外做加权折算——多一层折算只会在维度名对不上项目模块时
    悄悄改变分数含义。
    """
    raw_total = payload.get("total_score")
    if raw_total is not None:
        return _score(raw_total)
    if not dimensions:
        raise BusinessRuleError("模型既没给总分也没给维度分，无法评审")
    return _score(sum(Decimal(str(item["score"])) for item in dimensions))


# ------------------------------------------------------------------ 主流程


async def run_ai_review(session: AsyncSession, *, submission_id: int) -> AiReviewOutcome:
    """跑一次完整的 AI 评审：召回标准 → 打分 → 落库 → 结算。"""
    reviews = ReviewRecordRepository(session)
    if await reviews.latest_final(submission_id, review_kind="AI") is not None:
        raise ConflictError("这次提交已经有过 AI 评审结果，不再重复评审")

    context = await collect_submission_context(session, submission_id)
    criteria_docs = await load_criteria_docs(session, context.project_id)
    if not criteria_docs:
        raise BusinessRuleError(
            f"项目「{context.project_name}」还没有上传评分标准（附件用途选 SCORING_CRITERIA），无法评审"
        )

    criteria, warnings = await retrieve_criteria(session, context=context, docs=criteria_docs)
    if not criteria:
        raise BusinessRuleError("评分标准没有可用的切片内容，请先确认文档已解析入库")

    config = await settings_store.get_llm_config(session)
    if not config.configured:
        raise BusinessRuleError("还没配置大模型 api key：请在系统配置 ai.llm 里填 api_key")

    job = await _mark_job_processing(session, submission_id, config.model)
    try:
        content, request_id = await call_llm(
            config,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(context, criteria),
        )
        payload = parse_llm_json(content)
        dimensions = build_dimensions(payload, context)
        total_score = total_score_of(payload, dimensions)
    except Exception as exc:
        await _finish_job(session, job, ok=False, error=str(exc))
        raise

    conclusion, _pass_score = await resolve_conclusion(
        session, submission_id=submission_id, total_score=total_score
    )
    review = await reviews.create(
        {
            "submission_id": submission_id,
            "review_kind": "AI",
            "version_no": await reviews.next_version_no(submission_id, "AI"),
            "status": "FINAL",
            "reviewer_id": None,
            "total_score": total_score,
            "conclusion": conclusion,
            "comment": (str(payload.get("comment")).strip() if payload.get("comment") else None),
            "dimension_json": dimensions,
            "ai_model": config.model,
            "raw_json": {
                "model": config.model,
                "base_url": config.base_url,
                "criteria_doc_ids": [doc.id for doc in criteria_docs],
                "recalled": [
                    {
                        "chunk_id": item["chunk_id"],
                        "doc_id": item["doc_id"],
                        "heading_path": item.get("heading_path"),
                        "score": item.get("score"),
                    }
                    for item in criteria
                ],
                "attachments": attachment_summary(context),
                "llm_raw": content,
                "warnings": warnings,
            },
            "finished_at": now(),
        }
    )
    await finalize_review(session, review=review)
    await _finish_job(session, job, ok=True)
    return AiReviewOutcome(
        review_id=review.id or 0,
        submission_id=submission_id,
        total_score=total_score,
        conclusion=conclusion,
        criteria_doc_ids=[doc.id or 0 for doc in criteria_docs],
        recalled_chunks=len(criteria),
        model=config.model,
        warnings=warnings,
    )


async def _mark_job_processing(
    session: AsyncSession, submission_id: int, model_name: str
) -> ReviewAiJob | None:
    """把排队中的 AI 任务置为处理中；没有排队记录也不新建（评审结果才是关键产物）。"""
    jobs = ReviewAiJobRepository(session)
    job = await jobs.latest_queued(submission_id)
    if job is None:
        return None
    return await jobs.update(
        job,
        {
            "job_status": "PROCESSING",
            "model_name": model_name,
            "attempt_count": (job.attempt_count or 0) + 1,
        },
    )


async def _finish_job(
    session: AsyncSession, job: ReviewAiJob | None, *, ok: bool, error: str | None = None
) -> None:
    if job is None:
        return
    await ReviewAiJobRepository(session).update(
        job,
        {
            "job_status": "SUCCEED" if ok else "FAILED",
            "error_msg": None if ok else error,
            "finished_at": now(),
        },
    )


__all__ = [
    "LLM_EXTRA_HINT",
    "AiReviewOutcome",
    "StageAttachment",
    "StageAnswer",
    "SubmissionContext",
    "attachment_summary",
    "build_dimensions",
    "call_llm",
    "collect_submission_context",
    "llm_available",
    "load_criteria_docs",
    "load_stage_attachments",
    "parse_llm_json",
    "retrieve_criteria",
    "run_ai_review",
    "total_score_of",
]
