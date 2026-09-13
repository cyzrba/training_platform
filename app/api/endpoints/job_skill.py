"""岗位与技能成长接口：岗位、岗位技能、技能树与节点、成长规则、学生选岗与技能进度。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.core.time import now
from app.crud.account import UserRepository
from app.crud.job_skill import (
    GrowthRuleRepository,
    JobRepository,
    JobSkillRepository,
    SkillNodeDependencyRepository,
    SkillNodeRepository,
    SkillTreeRepository,
    StudentJobRepository,
    StudentSkillRepository,
)
from app.models.job_skill import (
    GrowthRule,
    Job,
    SkillNode,
    SkillTree,
)
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.job_skill import (
    GrowthRuleCreate,
    GrowthRuleRead,
    GrowthRuleUpdate,
    JobCreate,
    JobDetail,
    JobRead,
    JobSkillSetIn,
    JobUpdate,
    SkillNodeCreateIn,
    SkillNodeDependencySetIn,
    SkillNodeDetail,
    SkillNodeRead,
    SkillNodeUpdate,
    SkillTreeCreate,
    SkillTreeRead,
    SkillTreeUpdate,
    StudentJobDetail,
    StudentJobPrimaryIn,
    StudentJobSetIn,
    StudentSkillDetail,
    StudentSkillPatchIn,
)
from app.schemas.review import SkillRecalcResult
from app.services.skill import recalculate_student_skills

router = APIRouter(route_class=EnvelopeRoute, tags=["岗位技能"])


# --------------------------------------------------------------------- 依赖


def job_repo(db: DbSession) -> JobRepository:
    return JobRepository(db)


def job_skill_repo(db: DbSession) -> JobSkillRepository:
    return JobSkillRepository(db)


def skill_tree_repo(db: DbSession) -> SkillTreeRepository:
    return SkillTreeRepository(db)


def skill_node_repo(db: DbSession) -> SkillNodeRepository:
    return SkillNodeRepository(db)


def dependency_repo(db: DbSession) -> SkillNodeDependencyRepository:
    return SkillNodeDependencyRepository(db)


def growth_rule_repo(db: DbSession) -> GrowthRuleRepository:
    return GrowthRuleRepository(db)


def student_job_repo(db: DbSession) -> StudentJobRepository:
    return StudentJobRepository(db)


def student_skill_repo(db: DbSession) -> StudentSkillRepository:
    return StudentSkillRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


JobRepo = Annotated[JobRepository, Depends(job_repo)]
JobSkillRepo = Annotated[JobSkillRepository, Depends(job_skill_repo)]
SkillTreeRepo = Annotated[SkillTreeRepository, Depends(skill_tree_repo)]
SkillNodeRepo = Annotated[SkillNodeRepository, Depends(skill_node_repo)]
DependencyRepo = Annotated[SkillNodeDependencyRepository, Depends(dependency_repo)]
GrowthRuleRepo = Annotated[GrowthRuleRepository, Depends(growth_rule_repo)]
StudentJobRepo = Annotated[StudentJobRepository, Depends(student_job_repo)]
StudentSkillRepo = Annotated[StudentSkillRepository, Depends(student_skill_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


# --------------------------------------------------------------------- 工具


async def _job_or_404(jobs: JobRepository, job_id: int) -> Job:
    job = await jobs.get(job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在")
    return job


async def _tree_or_404(trees: SkillTreeRepository, tree_id: int) -> SkillTree:
    tree = await trees.get(tree_id)
    if tree is None:
        raise NotFoundError(f"技能树 {tree_id} 不存在")
    return tree


async def _node_or_404(nodes: SkillNodeRepository, node_id: int) -> SkillNode:
    node = await nodes.get(node_id)
    if node is None:
        raise NotFoundError(f"技能节点 {node_id} 不存在")
    return node


async def _rule_or_404(rules: GrowthRuleRepository, rule_id: int) -> GrowthRule:
    rule = await rules.get(rule_id)
    if rule is None:
        raise NotFoundError(f"成长规则 {rule_id} 不存在")
    return rule


async def _student_or_404(students: UserRepository, student_id: int) -> None:
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")


async def _assert_nodes_exist(nodes: SkillNodeRepository, node_ids: list[int]) -> None:
    for node_id in dict.fromkeys(node_ids):
        if await nodes.get(node_id) is None:
            raise BusinessRuleError(f"技能节点 {node_id} 不存在")


async def _assert_no_cycle(
    dependencies: SkillNodeDependencyRepository, node_id: int, prerequisite_ids: list[int]
) -> None:
    """自环与环路都要拦住：技能依赖必须是 DAG。"""
    if node_id in prerequisite_ids:
        raise BusinessRuleError("技能节点不能把自己设为前置技能")
    graph: dict[int, set[int]] = {}
    for source, prerequisite in await dependencies.all_edges():
        if source == node_id:
            continue  # 本次会被覆盖，先忽略旧边
        graph.setdefault(source, set()).add(prerequisite)

    stack = list(prerequisite_ids)
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current == node_id:
            raise BusinessRuleError("设置后会产生循环依赖，请检查前置技能")
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))


# ------------------------------------------------------------------- 岗位


@router.get("/jobs", response_model=ApiResponse[Page[JobRead]], summary="岗位分页列表")
async def list_jobs(
    jobs: JobRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="岗位名或场景模糊搜索")] = None,
    status_: Annotated[str | None, Query(alias="status", description="ENABLED / DISABLED")] = None,
    recommended_level: Annotated[str | None, Query(description="BASIC / ADVANCED / EXPANDED")] = None,
    direction_tag: Annotated[str | None, Query(description="岗位方向标签")] = None,
) -> Page[object]:
    return await jobs.list_jobs(
        page,
        keyword=keyword,
        status=status_,
        recommended_level=recommended_level,
        direction_tag=direction_tag,
    )


@router.post(
    "/jobs", response_model=ApiResponse[JobRead], status_code=status.HTTP_201_CREATED, summary="新建岗位"
)
async def create_job(payload: JobCreate, jobs: JobRepo) -> Job:
    if await jobs.by_name(payload.job_name) is not None:
        raise ConflictError(f"岗位 {payload.job_name} 已存在")
    return await jobs.create(payload.model_dump())


@router.get("/jobs/{job_id}", response_model=ApiResponse[JobDetail], summary="岗位详情（含技能数）")
async def get_job(job_id: int, jobs: JobRepo, job_skills: JobSkillRepo) -> dict:
    job = await _job_or_404(jobs, job_id)
    return {**job.model_dump(), "skill_count": await job_skills.count_of_job(job_id)}


@router.patch("/jobs/{job_id}", response_model=ApiResponse[JobRead], summary="更新岗位")
async def update_job(job_id: int, payload: JobUpdate, jobs: JobRepo) -> Job:
    job = await _job_or_404(jobs, job_id)
    data = payload.model_dump(exclude_unset=True)
    new_name = data.get("job_name")
    if new_name and new_name != job.job_name and await jobs.by_name(new_name) is not None:
        raise ConflictError(f"岗位 {new_name} 已存在")
    try:
        return await jobs.update(job, data)
    except IntegrityError as exc:
        raise ConflictError("更新失败：岗位名称重复") from exc


@router.delete("/jobs/{job_id}", response_model=ApiResponse[MessageOut], summary="删除岗位（软删）")
async def delete_job(job_id: int, jobs: JobRepo) -> MessageOut:
    job = await _job_or_404(jobs, job_id)
    await jobs.remove(job)
    return MessageOut(message="岗位已删除")


@router.get("/jobs/{job_id}/skills", response_model=ApiResponse[list[SkillNodeRead]], summary="查询岗位技能")
async def list_job_skills(job_id: int, jobs: JobRepo, job_skills: JobSkillRepo) -> list[SkillNode]:
    await _job_or_404(jobs, job_id)
    return await job_skills.list_nodes_of_job(job_id)


@router.put(
    "/jobs/{job_id}/skills", response_model=ApiResponse[list[SkillNodeRead]], summary="覆盖式设置岗位技能"
)
async def set_job_skills(
    job_id: int,
    payload: JobSkillSetIn,
    jobs: JobRepo,
    nodes: SkillNodeRepo,
    job_skills: JobSkillRepo,
) -> list[SkillNode]:
    await _job_or_404(jobs, job_id)
    await _assert_nodes_exist(nodes, payload.skill_node_ids)
    await job_skills.replace_skills(job_id, payload.skill_node_ids)
    return await job_skills.list_nodes_of_job(job_id)


@router.post(
    "/jobs/{job_id}/skills/{skill_node_id}",
    response_model=ApiResponse[MessageOut],
    status_code=status.HTTP_201_CREATED,
    summary="给岗位追加单个技能",
)
async def add_job_skill(
    job_id: int, skill_node_id: int, jobs: JobRepo, nodes: SkillNodeRepo, job_skills: JobSkillRepo
) -> MessageOut:
    await _job_or_404(jobs, job_id)
    node = await _node_or_404(nodes, skill_node_id)
    if await job_skills.link_exists(job_id, skill_node_id):
        raise ConflictError(f"岗位已关联技能 {node.node_name}")
    try:
        await job_skills.add_skill(job_id, skill_node_id)
    except IntegrityError as exc:
        raise ConflictError(f"岗位已关联技能 {node.node_name}") from exc
    return MessageOut(message="技能已关联")


@router.delete(
    "/jobs/{job_id}/skills/{skill_node_id}", response_model=ApiResponse[MessageOut], summary="解除岗位技能"
)
async def remove_job_skill(
    job_id: int, skill_node_id: int, jobs: JobRepo, job_skills: JobSkillRepo
) -> MessageOut:
    await _job_or_404(jobs, job_id)
    await job_skills.remove_skill(job_id, skill_node_id)
    return MessageOut(message="技能已解除")


# ----------------------------------------------------------------- 技能树


@router.get("/skill-trees", response_model=ApiResponse[Page[SkillTreeRead]], summary="技能树分页列表")
async def list_skill_trees(
    trees: SkillTreeRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="技能树编码或名称模糊搜索")] = None,
    status_: Annotated[str | None, Query(alias="status", description="ENABLED / DISABLED")] = None,
) -> Page[object]:
    return await trees.list_trees(page, keyword=keyword, status=status_)


@router.post(
    "/skill-trees",
    response_model=ApiResponse[SkillTreeRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建技能树",
)
async def create_skill_tree(payload: SkillTreeCreate, trees: SkillTreeRepo) -> SkillTree:
    if await trees.by_code(payload.tree_code) is not None:
        raise ConflictError(f"技能树编码 {payload.tree_code} 已存在")
    return await trees.create(payload.model_dump())


@router.get("/skill-trees/{tree_id}", response_model=ApiResponse[SkillTreeRead], summary="技能树详情")
async def get_skill_tree(tree_id: int, trees: SkillTreeRepo) -> SkillTree:
    return await _tree_or_404(trees, tree_id)


@router.patch("/skill-trees/{tree_id}", response_model=ApiResponse[SkillTreeRead], summary="更新技能树")
async def update_skill_tree(tree_id: int, payload: SkillTreeUpdate, trees: SkillTreeRepo) -> SkillTree:
    tree = await _tree_or_404(trees, tree_id)
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("tree_code")
    if new_code and new_code != tree.tree_code and await trees.by_code(new_code) is not None:
        raise ConflictError(f"技能树编码 {new_code} 已存在")
    return await trees.update(tree, data)


@router.delete("/skill-trees/{tree_id}", response_model=ApiResponse[MessageOut], summary="删除技能树（软删）")
async def delete_skill_tree(tree_id: int, trees: SkillTreeRepo) -> MessageOut:
    tree = await _tree_or_404(trees, tree_id)
    await trees.remove(tree)
    return MessageOut(message="技能树已删除")


@router.get(
    "/skill-trees/{tree_id}/nodes",
    response_model=ApiResponse[list[SkillNodeDetail]],
    summary="技能树下的节点列表",
)
async def list_skill_nodes(
    tree_id: int,
    trees: SkillTreeRepo,
    nodes: SkillNodeRepo,
    dependencies: DependencyRepo,
) -> list[dict]:
    tree = await _tree_or_404(trees, tree_id)
    items = await nodes.list_by_tree(tree_id)
    prereq_map = await dependencies.prerequisite_ids_of_many([node.id for node in items])
    return [
        {
            **node.model_dump(),
            "tree_name": tree.tree_name,
            "prerequisite_ids": prereq_map.get(node.id, []),
        }
        for node in items
    ]


@router.post(
    "/skill-trees/{tree_id}/nodes",
    response_model=ApiResponse[SkillNodeRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建技能节点",
)
async def create_skill_node(
    tree_id: int, payload: SkillNodeCreateIn, trees: SkillTreeRepo, nodes: SkillNodeRepo
) -> SkillNode:
    await _tree_or_404(trees, tree_id)
    if await nodes.by_code(payload.node_code) is not None:
        raise ConflictError(f"技能节点编码 {payload.node_code} 已存在")
    data = {"tree_id": tree_id, **payload.model_dump()}
    if data.get("unlock_rule_json") is None:
        data["unlock_rule_json"] = {}
    return await nodes.create(data)


@router.get("/skill-nodes/{node_id}", response_model=ApiResponse[SkillNodeDetail], summary="技能节点详情")
async def get_skill_node(
    node_id: int, nodes: SkillNodeRepo, trees: SkillTreeRepo, dependencies: DependencyRepo
) -> dict:
    node = await _node_or_404(nodes, node_id)
    tree = await trees.get(node.tree_id)
    return {
        **node.model_dump(),
        "tree_name": tree.tree_name if tree else None,
        "prerequisite_ids": await dependencies.prerequisite_ids(node_id),
    }


@router.patch("/skill-nodes/{node_id}", response_model=ApiResponse[SkillNodeRead], summary="更新技能节点")
async def update_skill_node(node_id: int, payload: SkillNodeUpdate, nodes: SkillNodeRepo) -> SkillNode:
    node = await _node_or_404(nodes, node_id)
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("node_code")
    if new_code and new_code != node.node_code and await nodes.by_code(new_code) is not None:
        raise ConflictError(f"技能节点编码 {new_code} 已存在")
    return await nodes.update(node, data)


@router.delete(
    "/skill-nodes/{node_id}", response_model=ApiResponse[MessageOut], summary="删除技能节点（软删）"
)
async def delete_skill_node(node_id: int, nodes: SkillNodeRepo) -> MessageOut:
    node = await _node_or_404(nodes, node_id)
    await nodes.remove(node)
    return MessageOut(message="技能节点已删除")


@router.get(
    "/skill-nodes/{node_id}/dependencies",
    response_model=ApiResponse[list[SkillNodeRead]],
    summary="查询前置技能",
)
async def list_dependencies(
    node_id: int, nodes: SkillNodeRepo, dependencies: DependencyRepo
) -> list[SkillNode]:
    await _node_or_404(nodes, node_id)
    prerequisite_ids = await dependencies.prerequisite_ids(node_id)
    result: list[SkillNode] = []
    for prerequisite_id in prerequisite_ids:
        node = await nodes.get(prerequisite_id)
        if node is not None:
            result.append(node)
    return result


@router.put(
    "/skill-nodes/{node_id}/dependencies",
    response_model=ApiResponse[list[SkillNodeRead]],
    summary="覆盖式设置前置技能（禁止自环与环路）",
)
async def set_dependencies(
    node_id: int,
    payload: SkillNodeDependencySetIn,
    nodes: SkillNodeRepo,
    dependencies: DependencyRepo,
) -> list[SkillNode]:
    await _node_or_404(nodes, node_id)
    unique_ids = list(dict.fromkeys(payload.prerequisite_node_ids))
    await _assert_nodes_exist(nodes, unique_ids)
    await _assert_no_cycle(dependencies, node_id, unique_ids)
    await dependencies.replace_prerequisites(node_id, unique_ids)
    return await list_dependencies(node_id, nodes, dependencies)


# --------------------------------------------------------------- 成长规则


@router.get("/growth-rules", response_model=ApiResponse[Page[GrowthRuleRead]], summary="成长规则分页列表")
async def list_growth_rules(rules: GrowthRuleRepo, page: PageDep) -> Page[object]:
    return await rules.list_page(page)


@router.post(
    "/growth-rules",
    response_model=ApiResponse[GrowthRuleRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建成长规则",
)
async def create_growth_rule(payload: GrowthRuleCreate, rules: GrowthRuleRepo) -> GrowthRule:
    if await rules.by_level_type(payload.level_type) is not None:
        raise ConflictError(f"层级 {payload.level_type} 的成长规则已存在")
    return await rules.create(payload.model_dump())


@router.get("/growth-rules/{rule_id}", response_model=ApiResponse[GrowthRuleRead], summary="成长规则详情")
async def get_growth_rule(rule_id: int, rules: GrowthRuleRepo) -> GrowthRule:
    return await _rule_or_404(rules, rule_id)


@router.patch("/growth-rules/{rule_id}", response_model=ApiResponse[GrowthRuleRead], summary="更新成长规则")
async def update_growth_rule(rule_id: int, payload: GrowthRuleUpdate, rules: GrowthRuleRepo) -> GrowthRule:
    rule = await _rule_or_404(rules, rule_id)
    data = payload.model_dump(exclude_unset=True)
    new_level = data.get("level_type")
    if new_level and new_level != rule.level_type and await rules.by_level_type(new_level) is not None:
        raise ConflictError(f"层级 {new_level} 的成长规则已存在")
    return await rules.update(rule, data)


@router.delete("/growth-rules/{rule_id}", response_model=ApiResponse[MessageOut], summary="删除成长规则")
async def delete_growth_rule(rule_id: int, rules: GrowthRuleRepo) -> MessageOut:
    rule = await _rule_or_404(rules, rule_id)
    await rules.remove(rule)
    return MessageOut(message="成长规则已删除")


# ------------------------------------------------------------ 学生选岗与技能


@router.get(
    "/students/{student_id}/jobs", response_model=ApiResponse[list[StudentJobDetail]], summary="学生选岗列表"
)
async def list_student_jobs(student_id: int, students: UserRepo, student_jobs: StudentJobRepo) -> list[dict]:
    await _student_or_404(students, student_id)
    return [
        {**selection.model_dump(), "job_name": job.job_name}
        for selection, job in await student_jobs.list_of_student(student_id)
    ]


@router.post(
    "/students/{student_id}/jobs",
    response_model=ApiResponse[StudentJobDetail],
    status_code=status.HTTP_201_CREATED,
    summary="学生选岗（可设为主岗位）",
)
async def select_student_job(
    student_id: int,
    payload: StudentJobSetIn,
    response: Response,
    students: UserRepo,
    jobs: JobRepo,
    student_jobs: StudentJobRepo,
) -> dict:
    """选岗：每选一个岗位加一条记录（student_id + job_id 唯一）。

    已经选过同一个岗位时不再报错，改为按 body 更新它的主岗位标记（幂等，方便前端"设为当前岗位"）。
    """
    await _student_or_404(students, student_id)
    job = await _job_or_404(jobs, payload.job_id)
    if payload.is_primary:
        await student_jobs.clear_primary(student_id)

    existing = await student_jobs.by_student_job(student_id, payload.job_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        updated = await student_jobs.update(
            existing,
            {
                "is_primary": payload.is_primary,
                "switched_at": now() if payload.is_primary else existing.switched_at,
            },
        )
        return {**updated.model_dump(), "job_name": job.job_name}

    selection = await student_jobs.create(
        {"student_id": student_id, "job_id": payload.job_id, "is_primary": payload.is_primary}
    )
    return {**selection.model_dump(), "job_name": job.job_name}


@router.patch(
    "/students/{student_id}/jobs/{student_job_id}",
    response_model=ApiResponse[StudentJobDetail],
    summary="设为主岗位 / 取消主岗位",
)
async def update_student_job(
    student_id: int,
    student_job_id: int,
    payload: StudentJobPrimaryIn,
    students: UserRepo,
    jobs: JobRepo,
    student_jobs: StudentJobRepo,
) -> dict:
    await _student_or_404(students, student_id)
    selection = await student_jobs.by_id(student_job_id)
    if selection is None or selection.student_id != student_id:
        raise NotFoundError(f"选岗记录 {student_job_id} 不存在")
    if payload.is_primary:
        await student_jobs.clear_primary(student_id, exclude_id=selection.id)
    updated = await student_jobs.update(
        selection,
        {
            "is_primary": payload.is_primary,
            "switched_at": now() if payload.is_primary else selection.switched_at,
        },
    )
    job = await jobs.get(updated.job_id)
    return {**updated.model_dump(), "job_name": job.job_name if job else None}


@router.delete(
    "/students/{student_id}/jobs/{student_job_id}",
    response_model=ApiResponse[MessageOut],
    summary="取消学生选岗",
)
async def remove_student_job(
    student_id: int, student_job_id: int, students: UserRepo, student_jobs: StudentJobRepo
) -> MessageOut:
    await _student_or_404(students, student_id)
    selection = await student_jobs.by_id(student_job_id)
    if selection is None or selection.student_id != student_id:
        raise NotFoundError(f"选岗记录 {student_job_id} 不存在")
    await student_jobs.remove(selection)
    return MessageOut(message="已取消该岗位")


@router.get(
    "/students/{student_id}/skills",
    response_model=ApiResponse[list[StudentSkillDetail]],
    summary="学生技能进度",
)
async def list_student_skills(
    student_id: int, students: UserRepo, student_skills: StudentSkillRepo
) -> list[dict]:
    await _student_or_404(students, student_id)
    return [
        {
            **skill.model_dump(),
            "node_code": node.node_code,
            "node_name": node.node_name,
            "tree_id": tree.id,
            "tree_name": tree.tree_name,
        }
        for skill, node, tree in await student_skills.list_of_student(student_id)
    ]


@router.patch(
    "/students/{student_id}/skills/{student_skill_id}",
    response_model=ApiResponse[StudentSkillDetail],
    summary="手工调整学生技能进度",
)
async def patch_student_skill(
    student_id: int,
    student_skill_id: int,
    payload: StudentSkillPatchIn,
    students: UserRepo,
    nodes: SkillNodeRepo,
    trees: SkillTreeRepo,
    student_skills: StudentSkillRepo,
) -> dict:
    await _student_or_404(students, student_id)
    skill = await student_skills.get(student_skill_id)
    if skill is None or skill.student_id != student_id:
        raise NotFoundError(f"技能进度记录 {student_skill_id} 不存在")
    data = payload.model_dump(exclude_unset=True)
    data.setdefault("source", "MANUAL")
    updated = await student_skills.update(skill, data)
    node = await nodes.get(updated.skill_node_id)
    tree = await trees.get(node.tree_id) if node else None
    return {
        **updated.model_dump(),
        "node_code": node.node_code if node else None,
        "node_name": node.node_name if node else None,
        "tree_id": tree.id if tree else None,
        "tree_name": tree.tree_name if tree else None,
    }


@router.post(
    "/students/{student_id}/skills/recalculate",
    response_model=ApiResponse[SkillRecalcResult],
    summary="按'完成项目数 ÷ 关联项目总数'重算学生技能进度",
)
async def recalculate_skills(student_id: int, db: DbSession, students: UserRepo) -> SkillRecalcResult:
    await _student_or_404(students, student_id)
    skills = await recalculate_student_skills(db, student_id)
    return SkillRecalcResult(student_id=student_id, updated=len(skills))


__all__ = ["router"]
