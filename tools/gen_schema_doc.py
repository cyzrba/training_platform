# -*- coding: utf-8 -*-
"""从 docs/database_schema_draft.sql 生成逐字段数据字典 docs/数据库表字段清单.md。"""

import io
import re

DDL_PATH = "docs/database_schema_draft.sql"
OUT_PATH = "docs/数据库表字段清单.md"

DOMAINS = [
    ("A 账户与权限", ["sys_user", "sys_role", "sys_permission", "sys_user_role", "sys_role_permission", "system_config"]),
    ("B 教学组织", ["class_info", "class_group", "class_student", "class_student_group"]),
    ("C 岗位与技能成长", ["job", "student_job", "skill_tree", "skill_node", "skill_node_dependency",
                          "student_skill", "job_skill", "project_skill", "growth_rule"]),
    ("D 实训项目", ["training_project", "project_stage_template", "project_module"]),
    ("E 闯关过程与评审", ["file_asset", "student_project", "training_attempt", "attempt_stage", "attempt_stage_file",
                          "project_submission", "review_record", "review_ai_job"]),
    ("F 证书台账", ["student_certificate"]),
    ("H AI 问答知识库", ["knowledge_doc", "knowledge_chunk", "ai_qa_session", "ai_qa_message", "ai_qa_citation"]),
    ("I 通知与审计", ["notification", "operation_log"]),
]

TABLE_CN = {
    "sys_user": "平台用户", "sys_role": "角色", "sys_permission": "权限点",
    "sys_user_role": "用户-角色关系", "sys_role_permission": "角色-权限关系", "system_config": "系统配置",
    "class_info": "班级", "class_group": "班级分组", "class_student": "在班学生",
    "class_student_group": "学生所在分组",
    "job": "岗位", "student_job": "学生-岗位选择", "skill_tree": "技能树体系", "skill_node": "技能节点（技能点）",
    "skill_node_dependency": "技能前置依赖", "student_skill": "学生技能进度", "job_skill": "岗位-技能要求",
    "project_skill": "项目-技能关系", "growth_rule": "成长规则",
    "training_project": "实训项目", "project_stage_template": "标准化模块库",
    "project_module": "项目模块组成",
    "file_asset": "文件元数据", "student_project": "学生实训记录", "training_attempt": "闯关轮次",
    "attempt_stage": "模块作答", "attempt_stage_file": "模块作答附件",
    "project_submission": "整单提交", "review_record": "评审记录（AI/教师）", "review_ai_job": "AI 评审任务",
    "student_certificate": "学生证书/台账",
    "knowledge_doc": "知识文档", "knowledge_chunk": "知识切片", "ai_qa_session": "AI 问答会话",
    "ai_qa_message": "问答消息", "ai_qa_citation": "回答引用来源",
    "notification": "站内通知", "operation_log": "操作审计日志",
}

TABLE_PURPOSE = {
    "sys_user": "单学院内的学生 / 教师 / 管理员账号；不存密码，身份来自学校统一认证 SSO。",
    "sys_role": "角色定义（教师、学生、管理员）；password 为后续 mock 预留，正式走 SSO。",
    "sys_permission": "扁平权限点（不做父子层级、不做排序）。",
    "sys_user_role": "用户与角色的多对多关系（含分配人）。",
    "sys_role_permission": "角色与权限点的多对多关系。",
    "system_config": "平台级键值配置（等级阈值、AI 模型参数、证书编号规则等）。",
    "class_info": "班级主数据：班级名称、负责教师、分组数量。",
    "class_group": "班级内部的分组（第 1 组、第 2 组…），教师可调整。",
    "class_student": "学生在班关系，含转出历史；任务发布按 ENROLLED 状态取人。",
    "class_student_group": "学生当前所在分组（调整分组即更新本表）。",
    "job": "岗位主数据（方向、推荐等级、适配场景、热度、启用状态）。",
    "student_job": "学生选择的岗位，支持多岗位与主岗位标记，切换岗位保留技能进度。",
    "skill_tree": "技能树的四大体系（光学成像系 / 传统算法系 / 深度学习系 / 系统部署系）。",
    "skill_node": "技能节点（技能点），技能树上的最小能力单元。",
    "skill_node_dependency": "技能点之间的前置依赖（DAG），决定“先学什么才能学什么”。",
    "student_skill": "学生每个技能点的进度与状态（未解锁 / 已激活 / 已精通）。",
    "job_skill": "岗位对技能点的要求：要求程度 + 权重，用于岗位匹配度计算。",
    "project_skill": "项目与技能点的多对多关系：完成关联项目即推进该技能进度。",
    "growth_rule": "按实训层级（基础 / 进阶 / 拓展）配置解锁条件、技能书满级标准、最大等级与等级说明。",
    "training_project": "实训项目主数据：层级、难度（1~5）、绑定岗位与状态；及格线统一在 growth_rule。",
    "project_stage_template": "标准化模块库：七大模块的默认必填、分值占比、作答要求与验收标准，供建项目时挑选。",
    "project_module": "项目模块组成：一条记录代表某个项目中挑选的一个模块（含顺序、必填、分值占比、作答要求）。",
    "file_asset": "文件元数据（MinIO 对象）的登记表：提交附件、头像、封面、证书文件、导入名单等。",
    "student_project": "学生个体的实训记录（教师导入名单时生成），承载填写进度、成绩与完成状态。",
    "training_attempt": "一次闯关轮次；重新挑战生成新的 attempt_no，历史保留。",
    "attempt_stage": "轮次内某个模块的作答内容与填写状态；全部必填模块填写完成才允许整单提交。",
    "attempt_stage_file": "模块作答与文件的关联（一个模块可带多个附件）。",
    "project_submission": "整单提交记录：一次提交包含该轮次全部模块的作答，可多次重提。",
    "review_record": "AI 或教师的整单评审记录，按 kind + version 保留多版本；维度明细存 dimension_json。",
    "review_ai_job": "AI 评审的异步任务记录，用于重试与对账。",
    "student_certificate": "已发放的电子证书与台账（作废、补发、有效期）。",
    "knowledge_doc": "RAG 知识库文档（课程资料等）。",
    "knowledge_chunk": "知识文档切片，向量库 Milvus 中的向量与此表 vector_id 对应。",
    "ai_qa_session": "学生的 AI 问答会话。",
    "ai_qa_message": "会话中的问答消息（用户 / 助手）。",
    "ai_qa_citation": "回答引用的知识来源，保证回答可信度与可追溯。",
    "notification": "站内通知（复审结果、技能点亮、任务发布、证书等）。",
    "operation_log": "关键操作审计日志（发布、撤回、审核、发证等），append-only。",
}

# 通用字段含义（按列名）
COMMON = {
    "id": "主键 ID", "created_at": "创建时间", "updated_at": "更新时间",
    "deleted_at": "软删除时间（为空表示未删除）", "sort_no": "排序号（越小越靠前）",
    "remark": "备注", "description": "说明 / 描述", "enabled": "是否启用",
    "status": "状态", "real_name": "姓名", "avatar_url": "头像地址", "phone": "手机号", "email": "邮箱",
    "last_login_at": "最近登录时间", "role_code": "角色编码（唯一）", "role_name": "角色名称",
    "parent_id": "上级 ID（树形自关联）", "perm_code": "权限编码（唯一）", "perm_name": "权限名称",
    "user_id": "用户 ID（sys_user.id）", "role_id": "角色 ID（sys_role.id）",
    "permission_id": "权限 ID（sys_permission.id）", "assigned_by": "分配人 ID（sys_user.id）",
    "config_key": "配置键（唯一）", "config_value": "配置值（JSON）", "updated_by": "最后修改人 ID（sys_user.id）",
    "class_name": "班级名称", "class_id": "班级 ID（class_info.id）", "group_no": "分组序号",
    "group_name": "分组名称", "capacity": "分组容量上限", "student_id": "学生 ID（sys_user.id）",
    "enrolled_at": "入班时间", "left_at": "离班时间", "class_student_id": "在班记录 ID（class_student.id）",
    "group_id": "分组 ID（class_group.id）", "assigned_at": "分配时间", "job_name": "岗位名称",
    "job_id": "岗位 ID（job.id）", "created_by": "创建人 ID（sys_user.id）", "creator_id": "创建人 ID（sys_user.id）",
    "last_publisher_id": "最近发布人 ID（sys_user.id）", "is_primary": "是否主岗位",
    "selected_at": "选择时间", "switched_at": "切换时间", "tree_code": "技能树编码（唯一）",
    "tree_name": "技能树名称", "tree_id": "技能树 ID（skill_tree.id）", "node_code": "技能节点编码（唯一）",
    "node_name": "技能节点名称", "node_id": "技能节点 ID（skill_node.id）",
    "prerequisite_node_id": "前置技能节点 ID（skill_node.id）", "skill_node_id": "技能节点 ID（skill_node.id）",
    "level": "等级", "activated_at": "点亮（激活）时间", "mastered_at": "精通时间", "weight": "权重",
    "level_description": "等级说明", "project_name": "项目名称", "project_id": "项目 ID（training_project.id）",
    "cover_url": "封面图地址", "template_id": "关卡模板 ID（project_stage_template.id）",
    "stage_key": "关卡 / 模块类型编码", "stage_name": "关卡 / 模块名称", "required": "是否必填",
    "default_required": "默认是否必填", "default_weight": "默认权重", "dimension_code": "评审维度编码",
    "dimension_name": "评审维度名称", "dimension_desc": "评审维度说明",
    "dimension_id": "评审维度 ID（review_dimension.id）", "task_no": "任务单编号（唯一）",
    "task_name": "任务单名称", "task_id": "任务单 ID（publish_task.id）", "published_at": "实际发布时间",
    "withdrawn_at": "撤回 / 下架时间", "withdraw_reason": "撤回原因", "withdrawn_by": "操作人 ID（sys_user.id）",
    "uploader_id": "上传人 ID（sys_user.id）", "bucket": "对象存储桶（MinIO bucket）",
    "object_key": "对象存储 Key", "original_name": "原始文件名", "content_type": "文件类型（MIME）",
    "size_bytes": "文件大小（字节）", "progress": "进度（0~100）", "best_score": "历史最高分",
    "attempt_count": "尝试 / 轮次数量", "started_at": "开始时间", "completed_at": "完成时间",
    "student_project_id": "学生实训记录 ID（student_project.id）",
    "attempt_id": "闯关轮次 ID（training_attempt.id）", "attempt_no": "轮次序号（从 1 递增）",
    "finished_at": "结束时间", "current_stage_no": "当前关卡序号", "completed_stage_count": "已通过关卡数",
    "total_score": "总分", "project_stage_id": "项目关卡 ID（project_stage.id）", "score": "得分",
    "passed_at": "通过时间", "submit_count": "提交次数", "attempt_stage_id": "关卡进度 ID（attempt_stage.id）",
    "submit_no": "提交序号（从 1 递增）", "content_text": "作答文本内容", "submitted_at": "提交时间",
    "reviewed_at": "评审完成时间", "submission_id": "提交记录 ID（stage_submission.id）",
    "file_asset_id": "文件 ID（file_asset.id）", "version_no": "版本号", "ai_model": "AI 模型名称",
    "review_record_id": "评审记录 ID（review_record.id）", "ai_score": "AI 评分", "ai_reason": "AI 评分理由",
    "teacher_score": "教师评分", "teacher_comment": "教师批注", "is_modified": "教师是否修改过该维度分数",
    "model_name": "模型名称", "request_id": "AI 服务请求 ID（对账用）", "error_msg": "错误信息",
    "certificate_no": "证书编号（唯一）", "issue_date": "发放日期", "expire_date": "有效期至",
    "issued_by": "发放人 ID（sys_user.id）", "revoke_reason": "作废原因", "rule_code": "积分规则编码（唯一）",
    "points": "积分值", "points_delta": "积分变动值（正加负减）", "balance_after": "变动后积分余额",
    "source_id": "来源业务 ID", "occurred_at": "发生时间", "title_code": "称号编码（唯一）",
    "title_name": "称号名称", "icon_url": "图标地址", "title_id": "称号 ID（title_def.id）",
    "acquired_at": "获得时间", "title": "标题", "biz_id": "关联业务 ID", "total_chunks": "切片总数",
    "uploaded_by": "上传人 ID（sys_user.id）", "doc_id": "知识文档 ID（knowledge_doc.id）",
    "chunk_index": "切片序号", "content": "内容", "content_hash": "内容哈希（去重 / 增量更新用）",
    "char_count": "字符数", "session_id": "会话 ID（ai_qa_session.id）", "prompt_tokens": "输入 Token 数",
    "completion_tokens": "输出 Token 数", "message_id": "消息 ID（ai_qa_message.id）",
    "chunk_id": "知识切片 ID（knowledge_chunk.id）", "relevance": "相关度（0~1）",
    "recipient_user_id": "接收人 ID（sys_user.id）", "read_at": "已读时间", "operator_id": "操作人 ID（sys_user.id）",
    "module": "业务模块", "action": "操作动作", "target_type": "操作对象类型", "target_id": "操作对象 ID",
    "ip": "客户端 IP", "user_agent": "客户端 User-Agent",
    "password": "预留密码（mock 登录 / 联调用，正式走 SSO）",
    "difficulty": "难度（1~5，1 最简单、5 最难）",
    "pass_score": "该层级项目完成及格线",
    "is_filled": "该模块是否已填写完成",
    "answer_text": "作答文本内容",
    "filled_at": "填写完成时间",
    "filled_stage_count": "已填写模块数",
    "dimension_json": "各维度得分与理由（JSON 数组）",
    "final_conclusion": "最终结论（PASS 通过 / FAIL 不通过）",
    "objection_reason": "学生对 AI 结果的异议说明",
    "is_starred": "教师标星",
    "reviewer_id": "评审人 ID（sys_user.id，AI 评审为空）",
    "conclusion": "评审结论（PASS / FAIL）",
    "comment": "评语 / 批注",
    "raw_json": "AI 原始返回快照（JSON）",
    "project_module_id": "项目模块 ID（project_module.id）",
}

# 同名不同义的字段按“表.字段”覆盖
OVERRIDE = {
    "sys_user.user_type": "用户类型（STUDENT 学生 / TEACHER 教师 / ADMIN 管理员）",
    "sys_user.status": "账号状态（ACTIVE 正常 / DISABLED 停用）",
    "sys_role.data_scope": "数据范围（ALL 全部 / CLASS 本班 / SELF 仅本人）",
    "class_info.status": "班级状态（ACTIVE 在读 / ARCHIVED 归档）",
    "class_student.status": "在班状态（ENROLLED 在班 / LEFT 已离班）",
    "job.status": "岗位状态（ENABLED 启用 / DISABLED 停用）",
    "job.recommended_level": "推荐学习等级（BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展）",
    "job.direction_tag": "岗位方向标签（如“工业视觉”）",
    "job.scene": "适配实训场景",
    "skill_tree.status": "技能树状态（ENABLED / DISABLED）",
    "skill_node.status": "技能节点状态（ENABLED / DISABLED）",
    "skill_node.unlock_note": "给学生看的解锁说明文案",
    "skill_node.unlock_rule_json": "未解锁提示用的规则（JSON），权威关系在 project_skill",
    "student_skill.level": "熟练等级（预留：三态模式下可用 0/1/2）",
    "student_skill.state": "技能状态（LOCKED 未解锁 / ACTIVATED 已激活 / MASTERED 已精通）",
    "student_skill.progress": "技能进度（0~100，= 完成数 ÷ 关联项目总数）",
    "student_skill.source": "进度来源（PROJECT / REVIEW / MANUAL）",
    "job_skill.weight": "该技能在岗位要求中的权重（用于匹配度）",
    "job_skill.required_level": "岗位要求程度（KNOW 了解 / FAMILIAR 熟悉 / MASTER 精通）",
    "review_dimension.default_weight": "该维度的默认权重",
    "review_dimension.stage_key": "适用的关卡 / 模块类型编码",
    "growth_rule.enabled": "该层级成长规则是否启用",
    "growth_rule.level_type": "实训层级（BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展）",
    "growth_rule.skill_max_level": "技能书最大等级（等级上限）",
    "training_project.status": "项目状态（DRAFT 草稿 / PUBLISHED 已发布 / OFF_SHELF 下架）",
    "training_project.pass_score": "完成及格线：全关通过且总分 ≥ 该值才判为完成",
    "training_project.project_level": "项目层级（BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展）",
    "training_project.difficulty": "难度（LOW 低 / MEDIUM 中 / HIGH 高）",
    "project_stage_template.stage_key": "模块类型编码（七大模块之一）",
    "project_stage_template.default_weight": "默认分值占比",
    "project_stage.stage_key": "模块类型编码（七大模块之一）",
    "project_stage.weight": "该关卡分值占比（项目级配置，启用模块合计 100）",
    "project_stage.enabled": "该关卡是否启用（关闭则不进入闯关、不计分）",
    "project_stage.prereq_stage_id": "前置关卡 ID（默认上一关，用于顺序解锁）",
    "publish_task.publish_mode": "发布方式（IMMEDIATE 即时 / SCHEDULED 定时）",
    "publish_task.status": "任务单状态（DRAFT / SCHEDULED 待发布 / PUBLISHED 已发布 / WITHDRAWN 已撤回）",
    "publish_task.job_id": "指定岗位范围（为空 = 全部岗位）",
    "publish_task.project_level": "指定项目层级（为空 = 全部层级）",
    "publish_task_target.group_id": "目标分组 ID（为空 = 全班）",
    "publish_task_project.status": "关联状态（ACTIVE 有效 / WITHDRAWN 已下架）",
    "student_project.status": "学生实训状态（NOT_STARTED 未开始 / IN_PROGRESS 进行中 / COMPLETED 已完成）",
    "student_project.publish_task_project_id": "来源发布记录 ID（publish_task_project.id，用于追溯是哪次任务发布的）",
    "student_project.progress": "项目进度（0~100，已通过关卡 ÷ 启用关卡）",
    "student_project.total_score": "当前总分（最新轮次成绩）",
    "student_project.best_score": "历史最高总分",
    "student_project.completed_score": "完成判定通过时的总分快照",
    "training_attempt.status": "轮次状态（IN_PROGRESS 进行中 / COMPLETED 已完成 / ABANDONED 已放弃）",
    "training_attempt.total_score": "本轮总分",
    "attempt_stage.status": "关卡状态（LOCKED 待解锁 / CURRENT 当前 / PASSED 已通过）",
    "attempt_stage.score": "该关卡最终得分（教师复审分优先于 AI 分）",
    "stage_submission.content_type": "作答类型（TEXT 文本 / FILE 文件 / BOTH 两者）",
    "stage_submission.status": "评审状态（PENDING_AI 待AI评审 / AI_PASSED AI通过 / AI_FAILED AI未通过 / PENDING_REVIEW 待复审 / REVIEWING 复审中 / REVIEWED 已复审）",
    "stage_submission.final_conclusion": "最终结论（PASS 通过 / FAIL 不通过）",
    "review_record.review_kind": "评审类型（AI 自动评审 / TEACHER 教师复审）",
    "review_record.status": "评审状态（DRAFT 草稿 / FINAL 最终）",
    "review_record.total_score": "本次评审总分（0~100）",
    "review_dimension_score.ai_score": "AI 在该维度的评分",
    "review_dimension_score.teacher_score": "教师在该维度的评分",
    "review_ai_job.model_name": "执行批阅的 AI 模型",
    "review_ai_job.job_status": "任务状态（QUEUED 排队 / PROCESSING 处理中 / SUCCEED 成功 / FAILED 失败）",
    "review_ai_job.attempt_count": "已尝试次数（用于重试与对账）",
    "point_rule.points": "该规则对应的积分值",
    "point_log.rule_code": "积分规则编码（point_rule.rule_code）",
    "point_log.source_id": "来源业务 ID（如 student_project.id / student_certificate.id）",
    "point_log.source_type": "来源类型（STAGE_PASS / PROJECT_COMPLETE / CERT_ISSUE…）",
    "title_def.status": "称号状态（ENABLED / DISABLED）",
    "knowledge_doc.title": "知识文档标题",
    "knowledge_chunk.content": "切片正文",
    "knowledge_chunk.status": "切片状态（READY / FAILED / DISABLED）",
    "ai_qa_session.title": "会话标题",
    "ai_qa_session.status": "会话状态（ACTIVE 进行中 / CLOSED 已结束）",
    "ai_qa_message.content": "消息内容（用户提问或 AI 回答）",
    "ai_qa_message.role": "消息角色（USER 用户提问 / ASSISTANT AI 回答）",
    "ai_qa_message.status": "消息状态（COMPLETED / FAILED）",
    "ai_qa_citation.sort_no": "引用排序",
    "notification.title": "通知标题",
    "notification.content": "通知内容",
    "operation_log.module": "业务模块（如 PROJECT / PUBLISH / REVIEW / CERT）",
    "operation_log.action": "操作动作（如 CREATE / PUBLISH / WITHDRAW / REVIEW / ISSUE）",
    "system_config.description": "配置说明",
    "class_student.enrolled_at": "入班时间",
    "class_student.left_at": "离班 / 转出时间（为空表示仍在班）",
    "student_job.is_primary": "是否当前主岗位（每名学生至多一个）",
    "file_asset.sha256": "文件 SHA256（去重 / 校验）",
    "file_asset.biz_type": "业务类型（SUBMISSION / AVATAR / PROJECT_COVER / CERT_PDF / IMPORT / REPORT）",
    "student_certificate.status": "证书状态（VALID 有效 / EXPIRED 过期 / REVOKED 作废 / RESSUED 已补发）",
    "student_certificate.replaced_by_id": "补发后新证书 ID（旧证指向新证）",
    "knowledge_doc.status": "文档状态（PARSING 解析中 / READY 可用 / FAILED 失败 / DISABLED 停用）",
    "knowledge_doc.biz_type": "关联对象类型（JOB 岗位 / COURSE 课程 / SYSTEM 系统）",
    "knowledge_doc.source": "来源（UPLOAD 文件上传 / TEXT 手工录入）",
    "file_asset.biz_type": "业务类型（SUBMISSION 提交附件 / AVATAR 头像 / PROJECT_COVER 封面 / CERT_PDF 证书 / IMPORT 导入名单 / REPORT 报告）",
    "notification.biz_type": "业务类型（REVIEW_RESULT / STAGE_RESULT / TASK_PUBLISH / CERT / POINT）",
}

ENUMS = [
    ("user_type", "STUDENT / TEACHER / ADMIN"),
    ("通用状态", "ACTIVE / DISABLED"),
    ("class_student.status", "ENROLLED / LEFT"),
    ("project_level", "BASIC（基础/普通）/ ADVANCED（进阶）/ EXPANDED（拓展）"),
    ("difficulty", "LOW / MEDIUM / HIGH"),
    ("training_project.status", "DRAFT / PUBLISHED / OFF_SHELF"),
    ("publish_mode", "IMMEDIATE（即时）/ SCHEDULED（定时）"),
    ("publish_task.status", "DRAFT / SCHEDULED / PUBLISHED / WITHDRAWN"),
    ("publish_task_project.status", "ACTIVE / WITHDRAWN"),
    ("student_project.status", "NOT_STARTED / IN_PROGRESS / COMPLETED"),
    ("training_attempt.status", "IN_PROGRESS / COMPLETED / ABANDONED"),
    ("attempt_stage.status", "LOCKED / CURRENT / PASSED"),
    ("stage_submission.status", "PENDING_AI / AI_PASSED / AI_FAILED / PENDING_REVIEW / REVIEWING / REVIEWED"),
    ("stage_submission.content_type", "TEXT / FILE / BOTH"),
    ("final_conclusion", "PASS / FAIL"),
    ("review_kind", "AI / TEACHER"),
    ("review_record.status", "DRAFT / FINAL"),
    ("review_ai_job.job_status", "QUEUED / PROCESSING / SUCCEED / FAILED"),
    ("student_skill.state", "LOCKED（未解锁）/ ACTIVATED（已激活）/ MASTERED（已精通）"),
    ("job_skill.required_level", "KNOW / FAMILIAR / MASTER"),
    ("student_certificate.status", "VALID / EXPIRED / REVOKED / RESSUED"),
    ("knowledge_doc.status", "PARSING / READY / FAILED / DISABLED"),
    ("ai_qa_session.status", "ACTIVE / CLOSED"),
    ("ai_qa_message.role", "USER / ASSISTANT"),
]

# ---- v0.3 调整：以下内容覆盖上面的旧配置（旧条目对已删除的表不再生效）----
COMMON.update({
    "password": "预留密码（mock 登录 / 联调用）",
    "difficulty": "难度（1~5）",
    "pass_score": "该层级项目完成及格线",
    "is_filled": "该模块是否已填写完成",
    "answer_text": "作答文本内容",
    "filled_at": "填写完成时间",
    "filled_stage_count": "已填写模块数",
    "dimension_json": "各维度得分与理由（JSON 数组）",
    "final_conclusion": "最终结论（PASS / FAIL）",
    "objection_reason": "学生对 AI 结果的异议说明",
    "is_starred": "教师标星",
    "reviewer_id": "评审人 ID（sys_user.id，AI 评审为空）",
    "conclusion": "评审结论（PASS / FAIL）",
    "comment": "评语 / 批注",
    "raw_json": "AI 原始返回快照（JSON）",
    "project_module_id": "项目模块 ID（project_module.id）",
    "detail_json": "变更前后快照（JSON）",
    "module_name": "模块名称（来自模块库或自定义）",
    "withdrawn_at": "撤回时间（学生撤回本次提交的时间）",
})

OVERRIDE.update({
    "sys_role.password": "预留密码（mock 登录 / 联调用；生产环境留空，正式走 SSO）",
    "sys_permission.perm_code": "权限编码（唯一，如 PROJECT_MANAGE / REVIEW_HANDLE）",
    "sys_permission.perm_name": "权限名称",
    "growth_rule.pass_score": "该层级项目的完成及格线（同层级所有项目共用）",
    "training_project.difficulty": "难度（1~5，1 最简单、5 最难）",
    "project_module.template_id": "来源模块模板 ID（project_stage_template.id；自定义模块为空）",
    "project_module.stage_key": "标准模块编码（来自模块库；自定义模块为空）",
    "project_module.module_name": "模块名称（来自模块库或自定义）",
    "project_module.stage_no": "项目内填写顺序（从 1 开始）",
    "project_module.requirement": "该模块的作答要求（可覆盖模板默认值）",
    "project_module.accept_standard": "该模块的验收标准（可覆盖模板默认值）",
    "project_module.required": "该模块是否必填（必填模块全部填写完成才允许整单提交）",
    "project_module.weight": "该模块分值占比（启用模块合计 100，供 AI 评审计分参考）",
    "project_module.enabled": "该模块是否启用（关闭则不进入闯关、不计分）",
    "student_project.status": "学生实训状态（NOT_STARTED 未开始 / IN_PROGRESS 填写中 / SUBMITTED 已提交待评审 / COMPLETED 已完成）",
    "student_project.progress": "填写进度（0~100，已填写模块 ÷ 启用模块）",
    "student_project.total_score": "当前总分（最新轮次成绩）",
    "training_attempt.status": "轮次状态（IN_PROGRESS 填写中 / SUBMITTED 已提交 / COMPLETED 已完成 / ABANDONED 已放弃）",
    "training_attempt.filled_stage_count": "本轮已填写模块数",
    "training_attempt.submitted_at": "整单提交时间",
    "attempt_stage.project_module_id": "项目模块 ID（project_module.id）",
    "attempt_stage.is_filled": "该模块是否已填写完成",
    "attempt_stage.answer_text": "该模块的作答文本内容",
    "attempt_stage.filled_at": "该模块填写完成时间",
    "attempt_stage_file.attempt_stage_id": "模块作答 ID（attempt_stage.id）",
    "project_submission.attempt_id": "闯关轮次 ID（training_attempt.id）",
    "project_submission.submit_no": "整单提交序号（从 1 递增，重新提交 +1）",
    "project_submission.status": "评审状态（PENDING_AI 待AI评审 / AI_PASSED AI通过 / AI_FAILED AI未通过 / PENDING_REVIEW 待复审 / REVIEWING 复审中 / REVIEWED 已复审 / WITHDRAWN 已撤回）",
    "project_submission.final_conclusion": "最终结论（PASS 通过 / FAIL 不通过）",
    "project_submission.total_score": "本次提交总分（0~100）",
    "project_submission.objection_reason": "学生对 AI 结果的异议说明（申请教师复审时填写）",
    "project_submission.is_starred": "教师标星（标星后进入审核列表关注区）",
    "project_submission.submitted_at": "整单提交时间",
    "project_submission.withdrawn_at": "学生撤回时间（撤回后实训记录回到填写中）",
    "project_submission.reviewed_at": "评审完成时间",
    "review_record.submission_id": "整单提交 ID（project_submission.id）",
    "review_record.dimension_json": "各维度得分与理由（JSON 数组，不再单独建维度字典表）",
    "review_record.raw_json": "AI 原始返回快照（JSON）",
    "review_ai_job.submission_id": "整单提交 ID（project_submission.id）",
    "job_skill.weight": "（已删除）",
})

ENUMS = [
    ("user_type", "STUDENT / TEACHER / ADMIN"),
    ("通用状态", "ACTIVE / DISABLED"),
    ("class_student.status", "ENROLLED / LEFT"),
    ("project_level", "BASIC（基础）/ ADVANCED（进阶）/ EXPANDED（拓展）"),
    ("training_project.difficulty", "1~5 的数字（1 最简单，5 最难）"),
    ("training_project.status", "DRAFT / PUBLISHED / OFF_SHELF"),
    ("student_project.status", "NOT_STARTED / IN_PROGRESS / SUBMITTED / COMPLETED"),
    ("training_attempt.status", "IN_PROGRESS / SUBMITTED / COMPLETED / ABANDONED"),
    ("project_submission.status", "PENDING_AI / AI_PASSED / AI_FAILED / PENDING_REVIEW / REVIEWING / REVIEWED / WITHDRAWN"),
    ("final_conclusion", "PASS / FAIL"),
    ("review_kind", "AI / TEACHER"),
    ("review_record.status", "DRAFT / FINAL"),
    ("review_ai_job.job_status", "QUEUED / PROCESSING / SUCCEED / FAILED"),
    ("student_skill.state", "LOCKED（未解锁）/ ACTIVATED（已激活）/ MASTERED（已精通）"),
    ("student_certificate.status", "VALID / EXPIRED / REVOKED / RESSUED"),
    ("knowledge_doc.status", "PARSING / READY / FAILED / DISABLED"),
    ("ai_qa_session.status", "ACTIVE / CLOSED"),
    ("ai_qa_message.role", "USER / ASSISTANT"),
    ("file_asset.biz_type", "SUBMISSION / AVATAR / CERT_PDF / IMPORT / REPORT"),
]

TYPE_WORDS = ("NOT NULL", "NULL", "DEFAULT", "REFERENCES", "GENERATED", "UNIQUE", "CHECK", "PRIMARY")


def parse_tables(ddl):
    tables = {}
    for block in re.split(r"(?m)^(?=CREATE TABLE )", ddl):
        m = re.match(r"CREATE TABLE\s+([a-z_]+)", block)
        if not m:
            continue
        name = m.group(1)
        body = block[: block.find(");") + 2]
        columns, constraints = [], []
        for line in body.split("\n")[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("CONSTRAINT", "UNIQUE", "PRIMARY KEY", "FOREIGN KEY", "CHECK")):
                constraints.append(stripped.rstrip(","))
                continue
            if ")" == stripped[0] or not re.match(r"^[a-z_]+\s", stripped):
                continue
            code, comment = (stripped.split("--", 1) + [""])[:2] if "--" in stripped else (stripped, "")
            code = code.rstrip().rstrip(",").strip()
            cm = re.match(r"^([a-z_]+)\s+(.*)$", code)
            if not cm:
                continue
            col, rest = cm.group(1), cm.group(2).strip()
            stop = re.search(r"\s+(?:" + "|".join(TYPE_WORDS) + r")\b", rest)
            ctype = rest[: stop.start()].strip() if stop else rest
            dm = re.search(
                r"\bDEFAULT\s+(.+?)(?=\s+(?:" + "|".join(TYPE_WORDS) + r")\b|$)", rest
            )
            default = dm.group(1).strip().rstrip(",") if dm else ""
            if "GENERATED BY DEFAULT AS IDENTITY" in rest:
                default = "自增"
            ref = re.search(r"REFERENCES\s+([a-z_]+)\s*\(\s*([a-z_]+)\s*\)", rest)
            columns.append({
                "name": col,
                "type": ctype,
                "notnull": "NOT NULL" in rest or "PRIMARY KEY" in rest,
                "default": default,
                "pk": "PRIMARY KEY" in rest,
                "unique": " UNIQUE" in (" " + rest),
                "ref": f"{ref.group(1)}.{ref.group(2)}" if ref else "",
                "comment": comment.strip(),
            })
        tables[name] = {"columns": columns, "constraints": constraints}
    return tables


def parse_indexes(ddl):
    result = {}
    for m in re.finditer(
        r"CREATE (UNIQUE )?INDEX ([a-z_]+)\s+ON ([a-z_]+)\s*\(([^)]*)\)\s*(WHERE [^;]+)?;", ddl
    ):
        unique, name, table, cols, where = m.groups()
        result.setdefault(table, []).append(
            ("UNIQUE " if unique else "") + f"{name} ({cols.strip()})" + (f" WHERE {where.strip()[6:]}" if where else "")
        )
    return result


def cell(value):
    return (value or "—").replace("|", "\\|")


def main():
    ddl = io.open(DDL_PATH, encoding="utf-8").read()
    tables = parse_tables(ddl)
    indexes = parse_indexes(ddl)
    table_comments = dict(re.findall(r"COMMENT ON TABLE (\w+) IS '([^']*)';", ddl))

    out = []
    out.append("# 岗位闯关式实训平台 · 数据库字段清单（P0 · 单学院）\n")
    out.append("> 本文件由 `docs/database_schema_draft.sql` 解析生成，字段与 DDL 一一对应；")
    out.append("> 用途：逐字段评审。表结构说明与业务流程见 `docs/数据库表架构设计-雏形.md`。\n")
    out.append("- 主键：`id`，`bigint` 自增（`GENERATED BY DEFAULT AS IDENTITY`）")
    out.append("- 时间：`created_at` / `updated_at` 为 `timestamptz`，默认 `now()`")
    out.append("- 主数据表带 `deleted_at` 软删除；流水 / 快照表不删除")
    out.append("- 状态字段用 `varchar` 存 code，取值范围见附录 A\n")

    total = sum(len(v) for _, v in DOMAINS)
    out.append(f"**表总数：{total} 张**（DDL 实际解析：{len(tables)} 张）\n")
    out.append("## 目录\n")
    out.append("| 域 | 表数 | 表 |")
    out.append("| --- | --- | --- |")
    for domain, names in DOMAINS:
        out.append(f"| {domain} | {len(names)} | " + "、".join(f"`{n}`" for n in names) + " |")
    out.append("")
    out.append("---\n")

    missing = []
    for di, (domain, names) in enumerate(DOMAINS, start=1):
        out.append(f"## {di}. {domain}\n")
        for ti, tname in enumerate(names, start=1):
            info = tables.get(tname)
            if info is None:
                out.append(f"### {di}.{ti} {tname}\n\n> ⚠️ DDL 中未找到该表\n")
                continue
            out.append(f"### {di}.{ti} {tname} —— {TABLE_CN.get(tname, '')}\n")
            purpose = table_comments.get(tname) or TABLE_PURPOSE.get(tname, "")
            if purpose:
                out.append(f"> {purpose}\n")
            out.append("| 字段 | 类型 | 可空 | 默认值 | 键 / 约束 | 说明（代表什么） |")
            out.append("| --- | --- | --- | --- | --- | --- |")
            for c in info["columns"]:
                keys = []
                if c["pk"]:
                    keys.append("PK")
                if c["ref"]:
                    keys.append(f"FK → {c['ref']}")
                if c["unique"]:
                    keys.append("UQ")
                remark = OVERRIDE.get(f"{tname}.{c['name']}") or c["comment"] or COMMON.get(c["name"])
                if not remark:
                    missing.append(f"{tname}.{c['name']}")
                    remark = ""
                out.append(
                    f"| {c['name']} | {cell(c['type'])} | {'否' if c['notnull'] else '是'} | "
                    f"{cell(c['default'])} | {'、'.join(keys) or '—'} | {cell(remark)} |"
                )
            out.append("")
            extra = []
            if info["constraints"]:
                extra.append("**表级约束**：")
                for c in info["constraints"]:
                    extra.append(f"- `{c}`")
                extra.append("")
            if indexes.get(tname):
                extra.append("**索引**：")
                for i in indexes[tname]:
                    extra.append(f"- `{i}`")
                extra.append("")
            if extra:
                out.extend(extra)
            out.append("")

    out.append("---\n")
    out.append("## 附录 A：枚举字典\n")
    out.append("| 枚举 | 取值 |")
    out.append("| --- | --- |")
    for name, codes in ENUMS:
        out.append(f"| {name} | {codes} |")
    out.append("")

    out.append("## 附录 B：外键关系一览\n")
    out.append("| 表.字段 | 引用 | 说明 |")
    out.append("| --- | --- | --- |")
    for _, names in DOMAINS:
        for tname in names:
            for c in tables.get(tname, {}).get("columns", []):
                if c["ref"]:
                    out.append(f"| {tname}.{c['name']} | {c['ref']} | 引用 {c['ref'].split('.')[0]} 表 |")
    out.append("")

    out.append("## 附录 C：通用字段约定\n")
    out.append("| 字段 | 约定 |")
    out.append("| --- | --- |")
    out.append("| id | 主键，`bigint` 自增；未来可平滑切换雪花 ID |")
    out.append("| created_at / updated_at | 创建、更新时间，`timestamptz`，默认 `now()` |")
    out.append("| deleted_at | 软删除时间；仅主数据表具备，流水 / 快照表不删除 |")
    out.append("| status / xxx_status | 状态 code，取值范围见附录 A，由后端常量管理 |")
    out.append("| sort_no | 展示排序，值越小越靠前 |")
    out.append("| remark / description | 备注与说明文本 |")
    out.append("")

    io.open(OUT_PATH, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print("written:", OUT_PATH)
    print("tables:", len(tables), "| missing remarks:", len(missing))
    if missing:
        print("\n".join(missing))


if __name__ == "__main__":
    main()
