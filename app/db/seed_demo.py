"""演示数据：教师 / 班级 / 学生 / 技能树与技能点 / 岗位 / 岗位技能 / 学生选岗（幂等）。

用法：``uv run python -m app.db.seed_demo``

用途：本地联调与前端演示。数据形态刻意做得不均匀 —— 有的班级带分组、有的不带，
有的老师带两个班；学生大多只选一个岗位，部分学生多看一个岗位，少数学生演示过"换主岗位"。
重复执行只补缺失的部分，不会重复插入。

账号说明：老师与学生都会建 ``sys_user`` 账号（默认密码见 settings.default_password），
老师挂 TEACHER 角色、学生挂 STUDENT 角色。

注：``skill_node_dependency``（前置技能）本轮不放演示数据。
"""

import asyncio
from decimal import Decimal
from typing import Any

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal, engine
from app.core.time import now
from app.crud.account import RoleRepository, UserRepository, UserRoleRepository
from app.crud.attempt import (
    AttemptStageRepository,
    FileAssetRepository,
    StudentProjectRepository,
)
from app.crud.job_skill import (
    JobRepository,
    JobSkillRepository,
    ProjectSkillRepository,
    SkillNodeRepository,
    SkillTreeRepository,
    StudentJobRepository,
)
from app.crud.organization import (
    ClassGroupRepository,
    ClassRepository,
    ClassStudentGroupRepository,
    ClassStudentRepository,
)
from app.crud.project import (
    ProjectFileRepository,
    ProjectModuleRepository,
    StageTemplateRepository,
    TrainingProjectRepository,
)
from app.crud.review import ReviewAiJobRepository, ReviewRecordRepository
from app.db.seed import SKILL_TREES, STAGE_TEMPLATES
from app.models.account import SysUser
from app.models.job_skill import StudentSkill
from app.services import storage
from app.services.attempt import (
    finalize_review,
    judge_conclusion,
    pass_score_of_project,
    raise_objection,
    save_stage_answer,
    start_attempt,
    submit_attempt,
)
from app.services.password import default_password_hash
from app.services.skill import recalculate_student_skills

# --------------------------------------------------------------------- 教师

TEACHERS: tuple[dict[str, Any], ...] = (
    {
        "user_no": "T2024001",
        "real_name": "张伟",
        "major_name": "人工智能技术应用",
        "phone": "13900000001",
        "email": "zhangwei@example.com",
        "remark": "人工智能教研室",
    },
    {
        "user_no": "T2024002",
        "real_name": "李娜",
        "major_name": "大数据技术",
        "phone": "13900000002",
        "email": "lina@example.com",
        "remark": "大数据教研室",
    },
    {
        "user_no": "T2024003",
        "real_name": "王强",
        "major_name": "软件技术",
        "phone": "13900000003",
        "email": "wangqiang@example.com",
        "remark": "软件教研室",
    },
    {
        "user_no": "T2024004",
        "real_name": "陈静",
        "major_name": "软件技术",
        "phone": "13900000004",
        "email": "chenjing@example.com",
        "remark": "软件教研室",
    },
)

# ------------------------------------------------------------------- 学生姓名池

STUDENT_NAMES: tuple[str, ...] = (
    "王浩然",
    "李思远",
    "张一鸣",
    "刘子涵",
    "陈奕辰",
    "赵语彤",
    "周泽楷",
    "吴梓萱",
    "徐若曦",
    "孙浩然",
    "马嘉懿",
    "朱雨桐",
    "胡一诺",
    "郭天宇",
    "何欣怡",
    "高子墨",
    "林雨欣",
    "罗子轩",
    "郑凯文",
    "梁雯雯",
    "谢明轩",
    "唐雨琪",
    "许志强",
    "韩佳怡",
    "冯俊杰",
    "邓紫萱",
    "曹宇轩",
    "彭思彤",
    "曾子谦",
    "肖雅静",
    "田浩宇",
    "董晨曦",
    "潘子豪",
    "袁梦琪",
    "蒋一帆",
    "蔡雨泽",
    "余思远",
    "杜佳明",
    "叶俊熙",
    "程雨婷",
    "苏子涵",
    "魏子豪",
    "吕佳琪",
    "丁俊豪",
    "任雨欣",
    "沈子轩",
    "姚佳明",
    "卢晨曦",
    "姜子豪",
    "崔雨蒙",
    "钟明宇",
    "谭佳琪",
    "陆子豪",
    "范雨轩",
    "汪俊豪",
    "石佳怡",
    "廖子轩",
    "贾雨婷",
    "夏明轩",
    "韦佳豪",
)

# --------------------------------------------------------------------- 班级
#: ``group_names`` 为空 = 该班级不分组建班

CLASSES: tuple[dict[str, Any], ...] = (
    {
        "class_name": "人工智能2401班",
        "grade_year": 2024,
        "teacher_no": "T2024001",
        "major_name": "人工智能技术应用",
        "remark": "按项目小组分组，共 4 组",
        "student_prefix": "20240101",
        "student_count": 12,
        "name_offset": 0,
        "group_names": ("第一组", "第二组", "第三组", "第四组"),
    },
    {
        "class_name": "人工智能2402班",
        "grade_year": 2024,
        "teacher_no": "T2024001",
        "major_name": "人工智能技术应用",
        "remark": "暂未分组",
        "student_prefix": "20240102",
        "student_count": 8,
        "name_offset": 12,
        "group_names": (),
    },
    {
        "class_name": "大数据技术2301班",
        "grade_year": 2023,
        "teacher_no": "T2024002",
        "major_name": "大数据技术",
        "remark": "按机房座位分组，共 3 组",
        "student_prefix": "20230101",
        "student_count": 11,
        "name_offset": 20,
        "group_names": ("第一组", "第二组", "第三组"),
    },
    {
        "class_name": "软件技术2403班",
        "grade_year": 2024,
        "teacher_no": "T2024003",
        "major_name": "软件技术",
        "remark": "暂未分组",
        "student_prefix": "20240103",
        "student_count": 9,
        "name_offset": 31,
        "group_names": (),
    },
    {
        "class_name": "软件技术2404班",
        "grade_year": 2024,
        "teacher_no": "T2024004",
        "major_name": "软件技术",
        "remark": "按实训方向分组，共 2 组",
        "student_prefix": "20240104",
        "student_count": 10,
        "name_offset": 40,
        "group_names": ("第一组", "第二组"),
    },
)

# ------------------------------------------------------------------- 技能节点
#: 技能树编码 -> 节点（node_code, node_name, description）
#: 注：skill_node_dependency（前置技能）本轮先不放数据

SKILL_NODES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "OPTICAL_IMAGING": (
        ("LIGHT_SELECT", "光源选型", "按材质与缺陷类型选择光源方案"),
        ("LENS_SELECT", "镜头选型", "按视野与精度要求选择镜头"),
        ("CAMERA_SELECT", "相机选型", "按分辨率与帧率选择相机"),
        ("IMAGE_TUNING", "成像调试", "调光圈、曝光与对焦，拿到可用图像"),
    ),
    "TRADITIONAL_ALGORITHM": (
        ("IMG_BASE", "图像基础", "灰度、通道、色彩空间等基本操作"),
        ("IMG_FILTER", "图像滤波", "去噪与平滑，抑制干扰"),
        ("EDGE_DETECT", "边缘检测", "提取轮廓与边界特征"),
        ("MORPHOLOGY", "形态学处理", "腐蚀膨胀开闭运算修形"),
        ("FEATURE_EXTRACT", "特征提取", "尺寸、面积、位置等特征量化"),
    ),
    "DEEP_LEARNING": (
        ("DL_BASE", "深度学习基础", "张量、梯度与训练流程"),
        ("DATA_LABEL", "数据标注", "标注规范与数据集制作"),
        ("CNN_BASIC", "CNN 原理", "卷积、池化与经典网络结构"),
        ("MODEL_TRAIN", "模型训练", "训练脚本、超参与训练监控"),
        ("MODEL_TUNE", "模型调优", "数据增强、调参与效果优化"),
    ),
    "SYSTEM_DEPLOYMENT": (
        ("LINUX_BASE", "Linux 基础", "常用命令与服务器环境配置"),
        ("DOCKER_BASE", "Docker 容器", "镜像构建与容器编排"),
        ("MODEL_SERVING", "模型服务化", "模型推理服务封装与调用"),
        ("PRODUCTION_DEBUG", "产线联调", "现场部署与问题定位"),
    ),
}

# ---------------------------------------------------------------------- 岗位

JOBS: tuple[dict[str, Any], ...] = (
    {
        "job_name": "工业视觉工程师",
        "direction_tag": "机器视觉",
        "recommended_level": "BASIC",
        "scene": "工业缺陷检测实训",
        "description": "面向产线缺陷检测，负责成像方案与视觉算法落地",
        "heat": 168,
        "skill_codes": (
            "IMG_BASE",
            "IMG_FILTER",
            "EDGE_DETECT",
            "MORPHOLOGY",
            "LIGHT_SELECT",
            "IMAGE_TUNING",
        ),
    },
    {
        "job_name": "视觉算法工程师",
        "direction_tag": "机器视觉",
        "recommended_level": "ADVANCED",
        "scene": "表面缺陷检测进阶",
        "description": "负责复杂缺陷的特征工程与算法选型调优",
        "heat": 142,
        "skill_codes": ("IMG_FILTER", "EDGE_DETECT", "FEATURE_EXTRACT", "MODEL_TRAIN", "MODEL_TUNE"),
    },
    {
        "job_name": "深度学习算法工程师",
        "direction_tag": "人工智能",
        "recommended_level": "ADVANCED",
        "scene": "工业质检模型训练",
        "description": "负责检测/分割/分类模型的数据、训练与调优",
        "heat": 155,
        "skill_codes": ("DL_BASE", "DATA_LABEL", "CNN_BASIC", "MODEL_TRAIN", "MODEL_TUNE"),
    },
    {
        "job_name": "光学成像工程师",
        "direction_tag": "光学成像",
        "recommended_level": "BASIC",
        "scene": "成像系统搭建",
        "description": "负责光源、镜头、相机选型与成像质量调优",
        "heat": 96,
        "skill_codes": ("LIGHT_SELECT", "LENS_SELECT", "CAMERA_SELECT", "IMAGE_TUNING"),
    },
    {
        "job_name": "视觉系统集成工程师",
        "direction_tag": "系统集成",
        "recommended_level": "EXPANDED",
        "scene": "产线部署与联调",
        "description": "负责模型服务化、设备通信与现场联调交付",
        "heat": 88,
        "skill_codes": ("LINUX_BASE", "DOCKER_BASE", "MODEL_SERVING", "PRODUCTION_DEBUG", "CAMERA_SELECT"),
    },
    {
        "job_name": "数据标注与训练工程师",
        "direction_tag": "人工智能",
        "recommended_level": "BASIC",
        "scene": "数据集建设",
        "description": "负责标注规范、数据集制作与基础模型训练",
        "heat": 74,
        "skill_codes": ("DATA_LABEL", "DL_BASE", "MODEL_TRAIN"),
    },
)

#: 班级 -> 该班默认主岗位
CLASS_PRIMARY_JOB: dict[str, str] = {
    "人工智能2401班": "深度学习算法工程师",
    "人工智能2402班": "工业视觉工程师",
    "大数据技术2301班": "数据标注与训练工程师",
    "软件技术2403班": "视觉系统集成工程师",
    "软件技术2404班": "视觉算法工程师",
}

#: 学生"再看一个岗位"时挑的备选池（非主岗位）
SECONDARY_JOB_POOL: tuple[str, ...] = (
    "工业视觉工程师",
    "视觉算法工程师",
    "光学成像工程师",
    "深度学习算法工程师",
    "视觉系统集成工程师",
)

# ---------------------------------------------------------------------- 项目
#: 项目 + 从模块库里挑的关卡（stage_key, weight, required）
#: 注：权重合计 100 的项目才是"可发布"状态，这里留一个草稿项目演示未配平的样子

PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "project_name": "工业缺陷检测实训",
        "project_level": "BASIC",
        "difficulty": 3,
        "teacher_no": "T2024001",
        "job_name": "工业视觉工程师",
        "description": "从需求分析到实训报告的完整闯关流程",
        "status": "PUBLISHED",
        "modules": (
            ("REQUIREMENT_ANALYSIS", 10, True),
            ("SOLUTION_DESIGN", 15, True),
            ("DATA_PROCESSING", 15, True),
            ("MODEL_TRAINING", 20, True),
            ("MODEL_OPTIMIZATION", 15, True),
            ("MODEL_TESTING", 15, True),
            ("REPORT_UPLOAD", 10, True),
        ),
    },
    {
        "project_name": "表面缺陷分类进阶",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024004",
        "job_name": "视觉算法工程师",
        "description": "面向复杂缺陷的分类模型训练与调优",
        "status": "PUBLISHED",
        "modules": (
            ("REQUIREMENT_ANALYSIS", 10, True),
            ("DATA_PROCESSING", 20, True),
            ("MODEL_TRAINING", 30, True),
            ("MODEL_OPTIMIZATION", 25, True),
            ("REPORT_UPLOAD", 15, True),
        ),
    },
    {
        "project_name": "成像系统搭建实训",
        "project_level": "BASIC",
        "difficulty": 2,
        "teacher_no": "T2024003",
        "job_name": "光学成像工程师",
        "description": "光源/镜头/相机选型与成像调试（草稿：权重还没配到 100）",
        "status": "DRAFT",
        "modules": (
            ("REQUIREMENT_ANALYSIS", 10, True),
            ("SOLUTION_DESIGN", 20, True),
            ("REPORT_UPLOAD", 10, True),
        ),
    },
)

#: 项目 -> 该项目要训练的技能节点（默认对齐它绑定岗位的技能，按项目特点略作增补）
PROJECT_SKILLS: dict[str, tuple[str, ...]] = {
    "工业缺陷检测实训": (
        "IMG_BASE",
        "IMG_FILTER",
        "EDGE_DETECT",
        "MORPHOLOGY",
        "FEATURE_EXTRACT",
        "LIGHT_SELECT",
        "IMAGE_TUNING",
    ),
    "表面缺陷分类进阶": (
        "DL_BASE",
        "DATA_LABEL",
        "IMG_FILTER",
        "EDGE_DETECT",
        "FEATURE_EXTRACT",
        "MODEL_TRAIN",
        "MODEL_TUNE",
    ),
    "成像系统搭建实训": ("LIGHT_SELECT", "LENS_SELECT", "CAMERA_SELECT", "IMAGE_TUNING"),
}

# --------------------------------------------------------------- 填写引导子标题
#: 项目关卡的填写引导子标题：**由教师手填**，模板不预设。
#: 这里把三个项目、15 个关卡的子标题都写全，并刻意做出跨项目差异
#: （检测类讲缺陷定义与打光，成像类讲光源镜头，分类类讲类别平衡与训练监控）。
PROJECT_MODULE_ITEMS: dict[tuple[str, str], list[dict[str, str]]] = {
    # ---- 工业缺陷检测实训（机器视觉检测方向）
    ("工业缺陷检测实训", "REQUIREMENT_ANALYSIS"): [
        {"title": "检测对象描述", "prompt": "工件名称、材质、尺寸范围"},
        {"title": "缺陷类型定义", "prompt": "划痕/凹坑/脏污的判定标准"},
        {"title": "检测精度要求", "prompt": "最小可检缺陷尺寸与误检率上限"},
    ],
    ("工业缺陷检测实训", "SOLUTION_DESIGN"): [
        {"title": "相机选型方案", "prompt": "面阵/线阵选择与分辨率计算"},
        {"title": "光源方案", "prompt": "条光/背光/同轴光的打光对比"},
        {"title": "镜头与视野计算", "prompt": "视野、工作距离与景深核算"},
    ],
    ("工业缺陷检测实训", "DATA_PROCESSING"): [
        {"title": "数据集构建", "prompt": "采集样本数量与正负样本比例"},
        {"title": "缺陷标注规范", "prompt": "标注框规则与边界处理"},
    ],
    ("工业缺陷检测实训", "MODEL_TRAINING"): [
        {"title": "检测模型选型", "prompt": "YOLO / Faster R-CNN 的取舍"},
        {"title": "训练配置与增强", "prompt": "超参、数据增强与训练轮次"},
    ],
    ("工业缺陷检测实训", "MODEL_OPTIMIZATION"): [
        {"title": "误检漏检优化", "prompt": "针对的问题与采用的优化手段"},
        {"title": "指标对比", "prompt": "优化前后 mAP 与漏检率对比"},
    ],
    ("工业缺陷检测实训", "MODEL_TESTING"): [
        {"title": "测试集与指标", "prompt": "测试集构成与评价指标"},
        {"title": "典型错误案例", "prompt": "误检/漏检样本与原因分析"},
    ],
    ("工业缺陷检测实训", "REPORT_UPLOAD"): [
        {"title": "报告结构与结论", "prompt": "章节安排与主要结论"},
        {"title": "问题与改进", "prompt": "遇到的问题与后续改进方向"},
    ],
    # ---- 表面缺陷分类进阶（深度学习分类方向）
    ("表面缺陷分类进阶", "REQUIREMENT_ANALYSIS"): [
        {"title": "缺陷类别清单", "prompt": "多分类任务的类别与样本量"},
        {"title": "数据规模与来源", "prompt": "采集设备、标注方式与数据分布"},
    ],
    ("表面缺陷分类进阶", "DATA_PROCESSING"): [
        {"title": "类别平衡策略", "prompt": "长尾类别的处理方式"},
        {"title": "数据增强方案", "prompt": "几何与颜色增强的组合"},
    ],
    ("表面缺陷分类进阶", "MODEL_TRAINING"): [
        {"title": "模型选型对比", "prompt": "ResNet / EfficientNet / ViT 的取舍"},
        {"title": "训练超参设置", "prompt": "学习率、batch size 与调度策略"},
        {"title": "训练过程监控", "prompt": "loss 曲线与过拟合判断"},
    ],
    ("表面缺陷分类进阶", "MODEL_OPTIMIZATION"): [
        {"title": "过拟合处理", "prompt": "正则化、早停与数据增广"},
        {"title": "分类指标对比", "prompt": "准确率、召回率与混淆矩阵"},
    ],
    ("表面缺陷分类进阶", "REPORT_UPLOAD"): [
        {"title": "实验记录整理", "prompt": "各组实验配置与结果汇总"},
        {"title": "结论与不足", "prompt": "主要结论与尚存不足"},
    ],
    # ---- 成像系统搭建实训（光学成像方向）
    ("成像系统搭建实训", "REQUIREMENT_ANALYSIS"): [
        {"title": "检测对象与视野要求", "prompt": "工件尺寸与所需视野范围"},
        {"title": "成像难点分析", "prompt": "反光、曲面、深色等成像难点"},
    ],
    ("成像系统搭建实训", "SOLUTION_DESIGN"): [
        {"title": "光源方案", "prompt": "均匀性与稳定性验证"},
        {"title": "镜头方案", "prompt": "放大倍率与畸变控制"},
        {"title": "相机方案", "prompt": "接口、带宽与触发方式"},
    ],
    ("成像系统搭建实训", "REPORT_UPLOAD"): [
        {"title": "选型依据汇总", "prompt": "光源/镜头/相机的选型对比"},
        {"title": "调试记录与结论", "prompt": "成像调试过程与最终结论"},
    ],
}

# --------------------------------------------------------------- 学生闯关进度
#: 学生做项目的安排：(班级, 项目名, 学生序号区间, 目标状态)
#: COMPLETED = AI 判通过 + 教师复核通过；OBJECTED = AI 判通过后学生提异议、等教师复核；
#: SUBMITTED = 已提交等 AI 评；IN_PROGRESS = 填了一部分
STUDENT_PROJECT_PLAN: tuple[tuple[str, str, tuple[int, int], str], ...] = (
    ("人工智能2401班", "工业缺陷检测实训", (0, 4), "COMPLETED"),
    ("人工智能2401班", "工业缺陷检测实训", (4, 7), "IN_PROGRESS"),
    ("人工智能2401班", "工业缺陷检测实训", (7, 8), "SUBMITTED"),
    ("人工智能2402班", "工业缺陷检测实训", (0, 3), "COMPLETED"),
    ("人工智能2402班", "工业缺陷检测实训", (3, 5), "IN_PROGRESS"),
    ("人工智能2402班", "工业缺陷检测实训", (5, 6), "OBJECTED"),
    ("软件技术2404班", "表面缺陷分类进阶", (0, 2), "COMPLETED"),
    ("大数据技术2301班", "表面缺陷分类进阶", (0, 2), "IN_PROGRESS"),
)

#: 演示用的异议留言
DEMO_OBJECTION = "我的光源对比实验写在方案设计第 3 小节，AI 评审里没有体现，申请教师人工复核。"

# --------------------------------------------------------------- 项目附件
#: 项目 -> 附件（报告模板 / 数据文件），内容是演示用的小文本
PROJECT_FILES: dict[str, tuple[dict[str, str], ...]] = {
    "工业缺陷检测实训": (
        {
            "file_kind": "REPORT_TEMPLATE",
            "name": "实训报告模板.md",
            "title": "实训报告模板",
            "remark": "按章节填写，最后一节写改进方向",
            "content": (
                "# 工业缺陷检测实训报告\n\n"
                "## 一、需求分析\n（检测对象、缺陷类型、精度要求）\n\n"
                "## 二、方案设计\n（相机、光源、镜头选型与打光对比）\n\n"
                "## 三、数据处理\n（数据集构建与标注规范）\n\n"
                "## 四、模型训练与优化\n（模型选型、训练配置、指标对比）\n\n"
                "## 五、测试与结论\n（测试集指标、典型错误案例、改进方向）\n"
            ),
        },
        {
            "file_kind": "DATASET",
            "name": "缺陷样本清单.csv",
            "title": "缺陷样本数据",
            "remark": "示例数据，仅用于演示上传与下载",
            "content": (
                "sample_id,defect_type,size_mm,label\n"
                "S0001,划痕,0.8,scratch\n"
                "S0002,凹坑,1.2,dent\n"
                "S0003,脏污,0.5,stain\n"
                "S0004,划痕,0.3,scratch\n"
            ),
        },
    ),
    "表面缺陷分类进阶": (
        {
            "file_kind": "REPORT_TEMPLATE",
            "name": "进阶实验报告模板.md",
            "title": "进阶报告模板",
            "remark": "需要附混淆矩阵与各类别指标",
            "content": (
                "# 表面缺陷分类进阶报告\n\n"
                "## 一、数据集说明（类别、样本量、分布）\n\n"
                "## 二、模型选型对比（ResNet / EfficientNet / ViT）\n\n"
                "## 三、训练配置与监控\n\n"
                "## 四、优化与指标（准确率、召回率、混淆矩阵）\n"
            ),
        },
        {
            "file_kind": "GUIDE",
            "name": "标注规范说明.md",
            "title": "标注规范说明",
            "remark": "标注前必读",
            "content": (
                "# 标注规范\n\n"
                "1. 每张图只标一个主缺陷类别；\n"
                "2. 边界超出图像的缺陷不参与训练；\n"
                "3. 类别存疑的交由教师确认后再入库。\n"
            ),
        },
    ),
    "成像系统搭建实训": (
        {
            "file_kind": "REPORT_TEMPLATE",
            "name": "成像实验报告模板.md",
            "title": "成像实验报告模板",
            "remark": "需附成像效果对比图",
            "content": (
                "# 成像系统搭建实验报告\n\n"
                "## 一、检测对象与视野要求\n\n"
                "## 二、光源方案与打光对比\n\n"
                "## 三、镜头与相机选型依据\n\n"
                "## 四、调试过程与结论\n"
            ),
        },
        {
            "file_kind": "DATASET",
            "name": "相机参数对比.csv",
            "title": "相机选型参数表",
            "remark": "选型对比用",
            "content": (
                "model,resolution,fps,interface,price_cny\n"
                "MV-CA050,2448x2048,60,GigE,4200\n"
                "MV-CA020,1624x1234,90,USB3,2800\n"
            ),
        },
    ),
}

#: 已通过项目的评审分数（演示用，按学生序号轮换）
DEMO_SCORES: tuple[str, ...] = ("92", "88", "85", "90")


async def _ensure_user(
    users: UserRepository,
    *,
    user_no: str,
    real_name: str,
    user_type: str,
    major_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    remark: str | None = None,
) -> tuple[SysUser, bool]:
    """按学号/工号取账号，不存在则建（默认密码）。返回 (账号, 是否新建)。"""
    existing = await users.get_by(user_no=user_no)
    if existing is not None:
        return existing, False
    created = await users.create(
        {
            "user_no": user_no,
            "real_name": real_name,
            "user_type": user_type,
            "major_name": major_name,
            "phone": phone,
            "email": email,
            "remark": remark,
            "password_hash": default_password_hash(),
        }
    )
    return created, True


async def _ensure_role(
    user_roles: UserRoleRepository,
    roles: RoleRepository,
    *,
    user_id: int,
    role_code: str,
) -> bool:
    """给账号补角色（已挂则跳过）。"""
    role = await roles.by_code(role_code)
    if role is None:
        return False
    assigned = {item.role_code for item in await user_roles.list_roles_of_user(user_id)}
    if role_code in assigned:
        return False
    await user_roles.create({"user_id": user_id, "role_id": role.id})
    return True


async def run_demo_seed(session: AsyncSession | None = None) -> dict[str, int]:
    """写入演示数据，返回各类新增数量。"""
    own_session = session is None
    session = session or SessionLocal()
    stats = {
        "teachers": 0,
        "classes": 0,
        "groups": 0,
        "students": 0,
        "enrollments": 0,
        "group_assignments": 0,
        "roles_assigned": 0,
        "skill_trees": 0,
        "stage_templates": 0,
        "skill_nodes": 0,
        "jobs": 0,
        "job_skills": 0,
        "student_jobs": 0,
        "projects": 0,
        "project_modules": 0,
        "project_skills": 0,
        "student_projects": 0,
        "attempts": 0,
        "submissions": 0,
        "reviews": 0,
        "student_skills": 0,
        "project_files": 0,
    }

    try:
        users = UserRepository(session)
        roles = RoleRepository(session)
        user_roles = UserRoleRepository(session)
        classes = ClassRepository(session)
        groups = ClassGroupRepository(session)
        enrollments = ClassStudentRepository(session)
        memberships = ClassStudentGroupRepository(session)
        trees = SkillTreeRepository(session)
        nodes = SkillNodeRepository(session)
        jobs = JobRepository(session)
        job_skills = JobSkillRepository(session)
        student_jobs = StudentJobRepository(session)
        templates = StageTemplateRepository(session)
        training_projects = TrainingProjectRepository(session)
        project_modules = ProjectModuleRepository(session)
        project_skills = ProjectSkillRepository(session)
        project_files = ProjectFileRepository(session)
        file_assets = FileAssetRepository(session)
        student_records = StudentProjectRepository(session)
        attempt_stages = AttemptStageRepository(session)
        reviews_repo = ReviewRecordRepository(session)
        ai_jobs_repo = ReviewAiJobRepository(session)
        class_teacher_no = {item["class_name"]: item["teacher_no"] for item in CLASSES}

        teacher_ids: dict[str, int] = {}
        for item in TEACHERS:
            teacher, created = await _ensure_user(
                users,
                user_no=item["user_no"],
                real_name=item["real_name"],
                user_type="TEACHER",
                major_name=item.get("major_name"),
                phone=item.get("phone"),
                email=item.get("email"),
                remark=item.get("remark"),
            )
            teacher_ids[item["user_no"]] = teacher.id
            stats["teachers"] += int(created)
            stats["roles_assigned"] += int(
                await _ensure_role(user_roles, roles, user_id=teacher.id, role_code="TEACHER")
            )

        # 技能树（基础种子里已有，这里兜底）+ 技能节点
        tree_ids: dict[str, int] = {}
        for item in SKILL_TREES:
            tree = await trees.by_code(item["tree_code"])
            if tree is None:
                tree = await trees.create(
                    {
                        "tree_code": item["tree_code"],
                        "tree_name": item["tree_name"],
                        "description": item["description"],
                    }
                )
                stats["skill_trees"] += 1
            tree_ids[item["tree_code"]] = tree.id

        # 模块库（基础种子里已有 7 个标准模块，这里兜底，项目才有模板可挑）
        for item in STAGE_TEMPLATES:
            if await templates.by_key(item["stage_key"]) is not None:
                continue
            await templates.create(
                {
                    "stage_key": item["stage_key"],
                    "stage_name": item["stage_name"],
                    "description": item.get("description"),
                    "default_required": item.get("default_required", True),
                    "default_weight": Decimal(str(item.get("default_weight", 0))),
                    "sort_no": item.get("sort_no", 0),
                }
            )
            stats["stage_templates"] += 1

        node_ids: dict[str, int] = {}
        for tree_code, node_items in SKILL_NODES.items():
            tree_id = tree_ids.get(tree_code)
            if tree_id is None:
                continue
            for node_code, node_name, node_desc in node_items:
                node = await nodes.by_code(node_code)
                if node is None:
                    node = await nodes.create(
                        {
                            "tree_id": tree_id,
                            "node_code": node_code,
                            "node_name": node_name,
                            "description": node_desc,
                        }
                    )
                    stats["skill_nodes"] += 1
                node_ids[node_code] = node.id

        # 岗位 + 岗位需要的技能
        job_ids: dict[str, int] = {}
        for item in JOBS:
            job = await jobs.by_name(item["job_name"])
            if job is None:
                job = await jobs.create(
                    {
                        "job_name": item["job_name"],
                        "direction_tag": item["direction_tag"],
                        "recommended_level": item["recommended_level"],
                        "scene": item["scene"],
                        "description": item["description"],
                        "heat": item["heat"],
                    }
                )
                stats["jobs"] += 1
            job_ids[item["job_name"]] = job.id

            linked = {node.id for node in await job_skills.list_nodes_of_job(job.id)}
            for node_code in item["skill_codes"]:
                node_id = node_ids.get(node_code)
                if node_id is None or node_id in linked:
                    continue
                await job_skills.add_skill(job.id, node_id)
                stats["job_skills"] += 1

        class_student_map: dict[str, list[int]] = {}
        for item in CLASSES:
            teacher_id = teacher_ids[item["teacher_no"]]
            classroom = await classes.get_by(class_name=item["class_name"])
            if classroom is None:
                classroom = await classes.create(
                    {
                        "class_name": item["class_name"],
                        "grade_year": item["grade_year"],
                        "head_teacher_id": teacher_id,
                        "group_count": len(item["group_names"]),
                        "remark": item["remark"],
                    }
                )
                stats["classes"] += 1

            # 分组（有的班级为空 = 不分组）
            class_groups = []
            for index, group_name in enumerate(item["group_names"], start=1):
                group = await groups.by_no(classroom.id, index)
                if group is None:
                    group = await groups.create(
                        {"class_id": classroom.id, "group_no": index, "group_name": group_name}
                    )
                    stats["groups"] += 1
                class_groups.append(group)

            # 学生：建账号 + 入班 + 可选分组（按序号轮流分配）
            class_students: list[int] = []
            class_student_map[item["class_name"]] = class_students
            for offset in range(item["student_count"]):
                index = item["name_offset"] + offset
                user_no = f"{item['student_prefix']}{offset + 1:02d}"
                student, created = await _ensure_user(
                    users,
                    user_no=user_no,
                    real_name=STUDENT_NAMES[index],
                    user_type="STUDENT",
                    major_name=item["major_name"],
                    phone=f"138{item['student_prefix']}{offset + 1:02d}",
                    email=f"{user_no}@example.com",
                )
                stats["students"] += int(created)
                stats["roles_assigned"] += int(
                    await _ensure_role(user_roles, roles, user_id=student.id, role_code="STUDENT")
                )
                class_students.append(student.id)

                enrollment = await enrollments.active_enrollment(classroom.id, student.id)
                if enrollment is None:
                    enrollment = await enrollments.create(
                        {"class_id": classroom.id, "student_id": student.id, "status": "ENROLLED"}
                    )
                    stats["enrollments"] += 1

                if class_groups:
                    target = class_groups[offset % len(class_groups)]
                    membership = await memberships.by_class_student(enrollment.id)
                    if membership is None or membership.group_id != target.id:
                        await memberships.set_group(enrollment.id, target.id)
                        stats["group_assignments"] += 1

                # 选岗：默认选中本班主岗位；每 3 人再看一个备选岗位（非主）；
                # 每 5 人演练一次"换主岗位"（备选岗位当主岗位，原岗位降级保留）
                default_job_id = job_ids[CLASS_PRIMARY_JOB[item["class_name"]]]
                secondary_job_id = job_ids[
                    SECONDARY_JOB_POOL[(item["name_offset"] + offset) % len(SECONDARY_JOB_POOL)]
                ]
                switched = offset > 0 and offset % 5 == 0 and secondary_job_id != default_job_id
                plan: list[tuple[int, bool]] = (
                    [(secondary_job_id, True), (default_job_id, False)]
                    if switched
                    else [(default_job_id, True)]
                )
                if not switched and offset % 3 == 0 and secondary_job_id != default_job_id:
                    plan.append((secondary_job_id, False))
                for job_id, is_primary in plan:
                    exists = await student_jobs.by_student_job(student.id, job_id)
                    if exists is not None:
                        continue
                    await student_jobs.create(
                        {
                            "student_id": student.id,
                            "job_id": job_id,
                            "is_primary": is_primary,
                            # 被降级的原岗位记下切换时间，保留"曾经选过"的历史
                            "switched_at": now() if switched and not is_primary else None,
                        }
                    )
                    stats["student_jobs"] += 1

        # 项目 + 项目关卡组成（关卡只能从模块库里挑）
        project_ids: dict[str, int] = {}
        for item in PROJECTS:
            project = await training_projects.by_name(item["project_name"])
            if project is None:
                project = await training_projects.create(
                    {
                        "project_name": item["project_name"],
                        "project_level": item["project_level"],
                        "difficulty": item["difficulty"],
                        "job_id": job_ids[item["job_name"]],
                        "description": item["description"],
                        "status": item["status"],
                        "creator_id": teacher_ids[item["teacher_no"]],
                    }
                )
                stats["projects"] += 1
            project_ids[item["project_name"]] = project.id

            for stage_no, (stage_key, weight, required) in enumerate(item["modules"], start=1):
                template = await templates.by_key(stage_key)
                if template is None:
                    continue
                # 子标题由教师手填（模板不预设），演示数据里直接给每个关卡配一套
                planned_items = PROJECT_MODULE_ITEMS.get((item["project_name"], stage_key), [])
                exists = await project_modules.by_template(project.id, template.id)
                if exists is not None:
                    # 演示数据以脚本为准：子标题对不上就同步（真人用的项目不受影响，只覆盖演示项目）
                    if planned_items and exists.items_json != planned_items:
                        await project_modules.update(exists, {"items_json": planned_items})
                    continue
                await project_modules.create(
                    {
                        "project_id": project.id,
                        "template_id": template.id,
                        "stage_no": stage_no,
                        "items_json": planned_items,
                        "required": required,
                        "weight": Decimal(str(weight)),
                    }
                )
                stats["project_modules"] += 1

            # 项目所需技能：对齐岗位技能并做少量增补
            for node_code in PROJECT_SKILLS.get(item["project_name"], ()):
                node_id = node_ids.get(node_code)
                if node_id is None or await project_skills.link_exists(project.id, node_id):
                    continue
                await project_skills.add_skill(project.id, node_id)
                stats["project_skills"] += 1

            # 项目附件：报告模板、数据文件（文件落对象存储，元数据进 file_asset）
            existing_files = {
                (link.file_kind, link.title) for link in await project_files.list_of_project(project.id)
            }
            for attachment in PROJECT_FILES.get(item["project_name"], ()):
                if (attachment["file_kind"], attachment["title"]) in existing_files:
                    continue
                stored = storage.save_bytes(
                    attachment["content"].encode("utf-8"),
                    filename=attachment["name"],
                    biz_type=attachment["file_kind"],
                    scope=storage.project_scope(project.id, project.project_name),
                )
                asset = await file_assets.create(
                    {
                        "bucket": stored.bucket,
                        "object_key": stored.object_key,
                        "original_name": attachment["name"],
                        "content_type": "text/plain; charset=utf-8",
                        "size_bytes": stored.size_bytes,
                        "sha256": stored.sha256,
                        "biz_type": attachment["file_kind"],
                    }
                )
                await project_files.create(
                    {
                        "project_id": project.id,
                        "file_asset_id": asset.id,
                        "file_kind": attachment["file_kind"],
                        "title": attachment["title"],
                        "remark": attachment["remark"],
                        "sort_no": await project_files.next_sort_no(project.id),
                    }
                )
                stats["project_files"] += 1

        # 学生闯关：走真实服务函数（开始闯关 → 填写 → 提交 → 评审定稿），保证与业务规则一致
        for class_name, project_name, (start, end), target in STUDENT_PROJECT_PLAN:
            project_id = project_ids.get(project_name)
            student_ids = class_student_map.get(class_name, [])
            if project_id is None or not student_ids:
                continue
            for offset in range(start, min(end, len(student_ids))):
                student_id = student_ids[offset]
                if await student_records.by_student_project(student_id, project_id) is not None:
                    continue  # 幂等：已有记录就跳过

                _, attempt = await start_attempt(session, student_id=student_id, project_id=project_id)
                stats["student_projects"] += 1
                stats["attempts"] += 1

                stage_rows = await attempt_stages.list_of_attempt(attempt.id)
                filled = (
                    len(stage_rows)
                    if target in {"SUBMITTED", "COMPLETED", "OBJECTED"}
                    else max(1, len(stage_rows) // 2)  # 进行中的填一半
                )
                for index, stage in enumerate(stage_rows):
                    if index >= filled:
                        break
                    await save_stage_answer(
                        session,
                        attempt_id=attempt.id,
                        stage_id=stage.id,
                        answer_text=f"{project_name} 第 {index + 1} 关的作答内容（演示数据）",
                        is_filled=True,
                    )
                if target == "IN_PROGRESS":
                    continue

                submission = await submit_attempt(session, attempt_id=attempt.id)
                stats["submissions"] += 1
                for job in await ai_jobs_repo.list_of_submission(submission.id):
                    await ai_jobs_repo.update(
                        job,
                        {"job_status": "SUCCEED", "model_name": "gpt-4o", "finished_at": now()},
                    )
                if target == "SUBMITTED":
                    continue  # 已提交待 AI 评审：留着 PENDING_AI 给学生/教师继续操作

                score = Decimal(DEMO_SCORES[offset % len(DEMO_SCORES)])
                # 结论按配置及格线算（演示分数都高于 60 线，所以都是 PASS）
                pass_score = await pass_score_of_project(session, project_id)
                conclusion = judge_conclusion(score, pass_score)
                dimensions = [
                    {"name": "内容完整性", "score": str(score - 3), "weight": "40", "reason": "要点齐全"},
                    {"name": "方案可行性", "score": str(score), "weight": "60", "reason": "方案可落地"},
                ]
                ai_review = await reviews_repo.create(
                    {
                        "submission_id": submission.id,
                        "review_kind": "AI",
                        "version_no": 1,
                        "status": "FINAL",
                        "total_score": score,
                        "conclusion": conclusion,
                        "comment": "AI 初审通过，建议教师复核",
                        "dimension_json": dimensions,
                        "ai_model": "gpt-4o",
                        "raw_json": {"source": "seed_demo"},
                        "finished_at": now(),
                    }
                )
                await finalize_review(session, review=ai_review)
                stats["reviews"] += 1
                if target == "OBJECTED":
                    # 学生对 AI 结果提异议 → 转教师复核（项目此时已被 AI 判完成）
                    await raise_objection(session, submission_id=submission.id, reason=DEMO_OBJECTION)
                    continue
                teacher_review = await reviews_repo.create(
                    {
                        "submission_id": submission.id,
                        "review_kind": "TEACHER",
                        "version_no": 1,
                        "status": "FINAL",
                        "reviewer_id": teacher_ids[class_teacher_no[class_name]],
                        "total_score": score,
                        "conclusion": conclusion,
                        "comment": "结构完整，方案可行，予以通过",
                        "dimension_json": dimensions,
                        "finished_at": now(),
                    }
                )
                await finalize_review(session, review=teacher_review)
                stats["reviews"] += 1

        # 学生技能点数据：按各自主岗位的技能范围重算（人人都有自己岗位的技能点记录）
        existing_skill_count_stmt = select(func.count()).select_from(StudentSkill)
        for item in CLASSES:
            job_name = CLASS_PRIMARY_JOB[item["class_name"]]
            job_spec = next(job for job in JOBS if job["job_name"] == job_name)
            node_id_list = [node_ids[code] for code in job_spec["skill_codes"] if code in node_ids]
            for student_id in class_student_map.get(item["class_name"], []):
                before = int(
                    (
                        await session.exec(
                            existing_skill_count_stmt.where(StudentSkill.student_id == student_id)
                        )
                    ).one()
                )
                rows = await recalculate_student_skills(session, student_id, skill_node_ids=node_id_list)
                stats["student_skills"] += max(0, len(rows) - before)

        await session.commit()
    finally:
        if own_session:
            await session.close()

    return stats


async def _seed_and_release() -> dict[str, int]:
    """写演示数据，结束后释放连接池，让 SQLite 把 -wal 落盘。

    WAL 模式下只有最后一个连接关闭时才会 checkpoint；CLI 进程若不释放引擎，
    data/app.db-wal 会一直留着，只读 app.db 的工具就会看不到刚写入的数据。
    """
    try:
        return await run_demo_seed()
    finally:
        await engine.dispose()


def main() -> None:
    stats = asyncio.run(_seed_and_release())
    print("演示数据写入完成：")
    for key, value in stats.items():
        print(f"  {key:18} 新增 {value}")


if __name__ == "__main__":
    main()
