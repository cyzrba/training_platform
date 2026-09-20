"""岗位 / 技能体系 / 技能点 / 实训项目 底稿（本平台这一类数据的唯一来源）。

数据全部取自《岗位能力与技能点归纳.md》（下称"归纳文档"），不再散落在各处：

- **技能体系 4 个**：归纳文档 §5.1 的四大体系（光学成像系 / 传统算法系 / 深度学习系 / 系统部署系）。
  归纳文档 §4.11 还提了 PM 单建「项目管理系」的方案，本平台不做 PM 岗，所以该体系连同
  ``PROJECT_MGMT`` 节点都不落库；
- **技能点 35 个**：§5.1 的 18 个已有节点 + §5.2 的 P0~P3 建议节点，去掉项目管理；
- **岗位 10 个**：§3.1 清单里的 11 个岗位，去掉 PM（机器视觉项目经理）；
- **岗位 × 技能点**：取 §6 矩阵里打 ●（核心必修）与 ○（需要了解）的格子。``job_skill``
  表没有"要求程度"字段（见 app/models/job_skill.py），两种要求都算岗位要求、都进匹配度分母；
- **实训项目 11 个**：归纳文档只点到了 3 个已有项目（§4.1 成像系统搭建实训、
  §4.2 工业缺陷检测实训、§4.3 表面缺陷分类进阶），其余是**示例数据**，按"每个岗位至少
  1 个已发布项目"补齐，使 10 个岗位点开都有项目、35 个技能点都有项目覆盖（否则技能点
  进度恒为 0、岗位体系里是空列表）。示例项目的技能点是按它的训练目标从 §6 矩阵里挑的。

落地方式：``run_growth_seed`` 幂等写入（按名称查重），由基础种子 ``app.db.seed`` 与
演示数据 ``app.db.seed_demo`` 共用；一键重建整库见 ``app.db.build_db``。

改这里的名字前请注意：技能点进度 = 该项目关联的已发布项目的完成度均值
（app/services/skill.py），岗位匹配度 = 岗位技能点进度的均值，所以改关联关系会直接
改变学生端看到的进度，不只是换文案。
"""

from decimal import Decimal
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal
from app.crud.job_skill import (
    JobRepository,
    JobSkillRepository,
    ProjectSkillRepository,
    SkillNodeRepository,
    SkillTreeRepository,
)
from app.crud.project import (
    ProjectModuleRepository,
    StageTemplateRepository,
    TrainingProjectRepository,
)
from app.models.account import SysUser

# ---------------------------------------------------------------------- 技能体系

#: 技能体系：(体系名, 说明)，取自归纳文档 §5.1
SKILL_TREES: tuple[dict[str, str], ...] = (
    {"tree_name": "光学成像系", "description": "光源、镜头、相机选型与成像调优"},
    {"tree_name": "传统算法系", "description": "图像预处理、特征提取与形态学处理"},
    {"tree_name": "深度学习系", "description": "检测、分割、分类模型的训练与优化"},
    {"tree_name": "系统部署系", "description": "模型部署、产线联调与工程化交付"},
)

# ---------------------------------------------------------------------- 技能节点

#: 体系名 -> ((节点名, 说明), ...)，说明取自归纳文档 §5.2 与各岗位明细的技能点表
SKILL_NODES: dict[str, tuple[tuple[str, str], ...]] = {
    "光学成像系": (
        ("光源选型", "按材质与缺陷类型选择条光 / 背光 / 同轴 / 环形等打光方案"),
        ("镜头选型", "按视野与精度要求选择镜头（FA / 远心 / 显微）"),
        ("相机选型", "按分辨率与帧率选择相机（面阵 / 线扫 / 3D）"),
        ("成像调试", "调光圈、曝光与对焦，拿到可用于检测的图像"),
        ("成像方案与可行性评估", "按检测 / 测量 / 定位 / 识别四类做可行性判定与风险预警"),
        ("复杂成像攻关", "高反光、曲面、复杂纹理与微小缺陷的打光与成像攻关"),
    ),
    "传统算法系": (
        ("图像基础", "灰度、通道、色彩空间等基本操作"),
        ("图像滤波", "去噪与平滑，抑制干扰"),
        ("边缘检测", "提取轮廓与边界特征"),
        ("形态学处理", "腐蚀膨胀开闭运算修形"),
        ("特征提取", "尺寸、面积、位置等特征量化"),
        ("模板匹配与定位", "模板标定、一键配方与位置纠偏"),
        ("几何测量与标定", "找线、找圆、Blob 分析、像素当量换算与畸变校正"),
        ("OCR 与条码识别", "字符识别、二维码 / 条码读取与码质评判"),
    ),
    "深度学习系": (
        ("深度学习基础", "张量、梯度与训练流程"),
        ("数据标注", "标注规范与数据集制作"),
        ("CNN 原理", "卷积、池化与经典网络结构"),
        ("模型训练", "训练脚本、超参与训练监控"),
        ("模型调优", "数据增强、调参与效果优化"),
        ("目标检测模型", "YOLO 系列从数据集到推理的全流程落地"),
        ("分割与分类模型", "分割 / 多分类模型的训练与指标分析"),
        ("模型轻量化与推理部署", "TensorRT / ONNX / 端侧推理加速"),
    ),
    "系统部署系": (
        ("Linux 基础", "常用命令与服务器环境配置"),
        ("Docker 容器", "镜像构建与容器编排"),
        ("模型服务化", "模型推理服务封装与调用"),
        ("产线联调", "现场部署、信号联锁与问题定位"),
        ("视觉软件工具应用", "视觉软件工具的参数调整、流程配置与脚本修改"),
        ("工控机与视觉接口", "多网口 / PoE / USB / 采集卡的匹配与占用率分析"),
        ("PLC 与 IO 通讯", "触发拍照、结果回传、信号互锁与 Modbus 通讯"),
        ("机械臂与手眼标定", "眼在手上 / 眼在手外、坐标转换与抓取偏移"),
        ("现场交付与验收", "现场问题统计、原因分析、验收标准与验收报告"),
        ("数据与质量分析", "CPK / GR&R / ARR / Correlation 等质量指标分析"),
        ("技术文档与报告", "需求清单、方案书、测试报告、FAT 报告与汇报材料"),
        ("开发语言与客户端", "C# + WinForm / QT + C++ 客户端与 SDK 二次开发"),
        ("成本与 BOM 测算", "硬件 + 人力 + 其他成本预估"),
    ),
}

# ------------------------------------------------------------------------- 岗位

#: 岗位（文案取自归纳文档 §4 各岗位明细的卡片文案），``skill_names`` 取自 §6 矩阵
JOBS: tuple[dict[str, Any], ...] = (
    {
        "job_name": "光学成像工程师",
        "direction_tag": "光学成像",
        "recommended_level": "BASIC",
        "scene": "成像系统搭建",
        "description": (
            "面向工业检测场景，负责光源、镜头、相机的选型与成像系统搭建，通过打光方案设计与"
            "成像调试，把缺陷在图像上打出来，为后续算法提供稳定可用的图像输入。"
        ),
        "heat": 96,
        "skill_names": (
            "光源选型",
            "镜头选型",
            "相机选型",
            "成像调试",
            "成像方案与可行性评估",
            "复杂成像攻关",
            "图像滤波",
            "几何测量与标定",
            "视觉软件工具应用",
            "数据与质量分析",
            "技术文档与报告",
        ),
    },
    {
        "job_name": "工业视觉工程师",
        "direction_tag": "机器视觉",
        "recommended_level": "BASIC",
        "scene": "工业缺陷检测实训",
        "description": (
            "面向产线缺陷检测场景，负责成像方案与视觉算法的落地：从看懂检测需求，到搭建成像"
            "系统，再到用传统图像算法完成缺陷的检出与判定，并输出可交付的检测方案。"
        ),
        "heat": 168,
        "skill_names": (
            "光源选型",
            "相机选型",
            "成像调试",
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "模板匹配与定位",
            "几何测量与标定",
            "OCR 与条码识别",
            "深度学习基础",
            "数据标注",
            "CNN 原理",
            "模型训练",
            "视觉软件工具应用",
            "数据与质量分析",
            "技术文档与报告",
        ),
    },
    {
        "job_name": "视觉算法工程师",
        "direction_tag": "机器视觉",
        "recommended_level": "ADVANCED",
        "scene": "表面缺陷检测进阶",
        "description": (
            "面向复杂缺陷场景，负责特征工程与算法选型调优：在传统图像算法与深度学习之间做"
            "取舍，处理低对比度、复杂背景、小样本等问题，并通过参数优化与效果验证把误检率、"
            "漏检率压到产线可接受的范围。"
        ),
        "heat": 142,
        "skill_names": (
            "光源选型",
            "成像调试",
            "复杂成像攻关",
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "模板匹配与定位",
            "几何测量与标定",
            "OCR 与条码识别",
            "深度学习基础",
            "数据标注",
            "CNN 原理",
            "模型训练",
            "模型调优",
            "目标检测模型",
            "分割与分类模型",
            "模型轻量化与推理部署",
            "Linux 基础",
            "视觉软件工具应用",
            "数据与质量分析",
            "技术文档与报告",
            "开发语言与客户端",
        ),
    },
    {
        "job_name": "深度学习算法工程师",
        "direction_tag": "人工智能",
        "recommended_level": "ADVANCED",
        "scene": "工业质检模型训练",
        "description": (
            "面向工业质检的模型训练场景，负责数据集的构建与标注规范、模型选型与训练、效果"
            "调优与轻量化部署。需要能把检测任务翻译成训练任务，并从 loss 曲线、混淆矩阵、"
            "误检样本里找到问题。"
        ),
        "heat": 155,
        "skill_names": (
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "OCR 与条码识别",
            "深度学习基础",
            "数据标注",
            "CNN 原理",
            "模型训练",
            "模型调优",
            "目标检测模型",
            "分割与分类模型",
            "模型轻量化与推理部署",
            "Linux 基础",
            "Docker 容器",
            "模型服务化",
            "数据与质量分析",
            "技术文档与报告",
            "开发语言与客户端",
        ),
    },
    {
        "job_name": "视觉系统集成工程师",
        "direction_tag": "系统集成",
        "recommended_level": "EXPANDED",
        "scene": "产线部署与联调",
        "description": (
            "面向产线部署与联调场景，负责模型服务化、设备通讯与现场集成：把训练好的模型封装"
            "成可调用的推理服务，打通与相机、工控机、PLC、分选机构的信号链路，完成整线联调"
            "与稳定性验证，直至客户验收。"
        ),
        "heat": 88,
        "skill_names": (
            "相机选型",
            "成像调试",
            "几何测量与标定",
            "OCR 与条码识别",
            "深度学习基础",
            "模型训练",
            "目标检测模型",
            "模型轻量化与推理部署",
            "Linux 基础",
            "Docker 容器",
            "模型服务化",
            "产线联调",
            "视觉软件工具应用",
            "工控机与视觉接口",
            "PLC 与 IO 通讯",
            "机械臂与手眼标定",
            "现场交付与验收",
            "数据与质量分析",
            "技术文档与报告",
            "开发语言与客户端",
            "成本与 BOM 测算",
        ),
    },
    {
        "job_name": "数据标注与训练工程师",
        "direction_tag": "人工智能",
        "recommended_level": "BASIC",
        "scene": "数据集建设",
        "description": (
            "面向工业质检的数据集建设场景，负责标注规范制定、数据集制作与基础模型训练。需要"
            "理解缺陷定义与客户判定标准，把主观的判定变成客观、可复现的标注规则，并跑通一次"
            "训练流程。"
        ),
        "heat": 74,
        "skill_names": (
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "深度学习基础",
            "数据标注",
            "CNN 原理",
            "模型训练",
            "模型调优",
            "目标检测模型",
            "分割与分类模型",
            "数据与质量分析",
            "技术文档与报告",
        ),
    },
    {
        "job_name": "机器视觉应用工程师",
        "direction_tag": "机器视觉（现场）",
        "recommended_level": "BASIC",
        "scene": "客户现场装机与调试",
        "description": (
            "面向客户现场的设备部署与调试场景，负责视觉系统的安装、调试与日常维护：使用成熟的"
            "视觉软件工具完成流程配置与参数调整，处理现场常见的图像质量与检测异常问题，配合"
            "后端团队定位并反馈问题。"
        ),
        "heat": 136,
        "skill_names": (
            "光源选型",
            "镜头选型",
            "相机选型",
            "成像调试",
            "成像方案与可行性评估",
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "模板匹配与定位",
            "几何测量与标定",
            "OCR 与条码识别",
            "Linux 基础",
            "产线联调",
            "视觉软件工具应用",
            "工控机与视觉接口",
            "PLC 与 IO 通讯",
            "机械臂与手眼标定",
            "现场交付与验收",
            "数据与质量分析",
            "技术文档与报告",
        ),
    },
    {
        "job_name": "视觉 FAE / 交付工程师",
        "direction_tag": "交付服务",
        "recommended_level": "ADVANCED",
        "scene": "客户现场交付与问题定位",
        "description": (
            "面向客户现场的全流程交付，负责方案落地、现场实施、问题定位与客户沟通。需要能快速"
            "判断一个现场问题属于光学成像、算法精度、机械电气还是通讯配置，给出初步分析与"
            "解决方案，并推动后端团队闭环解决。"
        ),
        "heat": 124,
        "skill_names": (
            "光源选型",
            "相机选型",
            "成像调试",
            "成像方案与可行性评估",
            "复杂成像攻关",
            "图像基础",
            "图像滤波",
            "边缘检测",
            "形态学处理",
            "特征提取",
            "模板匹配与定位",
            "几何测量与标定",
            "OCR 与条码识别",
            "深度学习基础",
            "数据标注",
            "Linux 基础",
            "Docker 容器",
            "模型服务化",
            "产线联调",
            "视觉软件工具应用",
            "工控机与视觉接口",
            "PLC 与 IO 通讯",
            "机械臂与手眼标定",
            "现场交付与验收",
            "数据与质量分析",
            "技术文档与报告",
            "开发语言与客户端",
            "成本与 BOM 测算",
        ),
    },
    {
        "job_name": "视觉方案工程师",
        "direction_tag": "解决方案",
        "recommended_level": "ADVANCED",
        "scene": "售前方案与可行性评估",
        "description": (
            "面向售前技术对接与方案设计，负责把客户模糊的需求拆解为可执行的检测方案：完成"
            "可行性评估、成像方案设计与硬件选型、成本与 BOM 测算，输出技术方案书并对风险点"
            "提前预警。"
        ),
        "heat": 108,
        "skill_names": (
            "光源选型",
            "镜头选型",
            "相机选型",
            "成像调试",
            "成像方案与可行性评估",
            "复杂成像攻关",
            "图像基础",
            "模板匹配与定位",
            "几何测量与标定",
            "视觉软件工具应用",
            "工控机与视觉接口",
            "现场交付与验收",
            "数据与质量分析",
            "技术文档与报告",
            "成本与 BOM 测算",
        ),
    },
    {
        "job_name": "视觉软件工程师",
        "direction_tag": "系统集成",
        "recommended_level": "ADVANCED",
        "scene": "视觉软件开发",
        "description": (
            "面向视觉软件的开发场景，负责算法运行软件的设计与开发：完成图像采集、参数配置、"
            "结果展示、数据存储与报表导出等功能，对接相机与硬件 SDK，输出稳定可维护的客户端"
            "程序。"
        ),
        "heat": 130,
        "skill_names": (
            "相机选型",
            "图像基础",
            "模板匹配与定位",
            "几何测量与标定",
            "OCR 与条码识别",
            "模型轻量化与推理部署",
            "Linux 基础",
            "Docker 容器",
            "模型服务化",
            "产线联调",
            "视觉软件工具应用",
            "工控机与视觉接口",
            "PLC 与 IO 通讯",
            "现场交付与验收",
            "技术文档与报告",
            "开发语言与客户端",
        ),
    },
)

# ------------------------------------------------------------------------- 项目

#: 示例实训项目：``modules`` 里的权重合计 100 才能发布（草稿故意没配平，用来演示教师端配置）
PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "project_name": "工业缺陷检测实训",
        "project_level": "BASIC",
        "difficulty": 3,
        "teacher_no": "T2024001",
        "job_name": "工业视觉工程师",
        "description": "从需求分析到实训报告的完整闯关流程（归纳文档 §4.2 的已有项目）",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 10, True),
            ("方案设计", 15, True),
            ("数据处理", 15, True),
            ("模型训练", 20, True),
            ("模型优化", 15, True),
            ("模型测试", 15, True),
            ("实训报告上传", 10, True),
        ),
    },
    {
        "project_name": "表面缺陷分类进阶",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024004",
        "job_name": "视觉算法工程师",
        "description": "面向复杂缺陷的分类模型训练与调优（归纳文档 §4.3 的已有项目）",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 10, True),
            ("数据处理", 20, True),
            ("模型训练", 30, True),
            ("模型优化", 25, True),
            ("实训报告上传", 15, True),
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
            ("需求分析", 10, True),
            ("方案设计", 20, True),
            ("实训报告上传", 10, True),
        ),
    },
    {
        "project_name": "光学成像方案设计与选型实训",
        "project_level": "BASIC",
        "difficulty": 3,
        "teacher_no": "T2024003",
        "job_name": "光学成像工程师",
        "description": "按检测需求做可行性评估，完成光源/镜头/相机选型与成像实验记录",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 20, True),
            ("方案设计", 40, True),
            ("数据处理", 20, True),
            ("实训报告上传", 20, True),
        ),
    },
    {
        "project_name": "工业质检模型训练与部署实训",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024001",
        "job_name": "深度学习算法工程师",
        "description": "从数据集构建到模型训练、轻量化与推理部署的检测模型全流程",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 10, True),
            ("数据处理", 20, True),
            ("模型训练", 30, True),
            ("模型优化", 20, True),
            ("模型测试", 10, True),
            ("实训报告上传", 10, True),
        ),
    },
    {
        "project_name": "缺陷数据集标注与训练实训",
        "project_level": "BASIC",
        "difficulty": 2,
        "teacher_no": "T2024002",
        "job_name": "数据标注与训练工程师",
        "description": "制定标注规范、制作数据集并跑通一次基础训练流程",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 15, True),
            ("数据处理", 35, True),
            ("模型训练", 25, True),
            ("实训报告上传", 25, True),
        ),
    },
    {
        "project_name": "产线视觉系统安装调试实训",
        "project_level": "BASIC",
        "difficulty": 3,
        "teacher_no": "T2024003",
        "job_name": "机器视觉应用工程师",
        "description": "在客户现场完成视觉工具流程搭建、参数调整与常见异常处理",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 20, True),
            ("方案设计", 30, True),
            ("模型测试", 25, True),
            ("实训报告上传", 25, True),
        ),
    },
    {
        "project_name": "客户现场交付与验收实训",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024002",
        "job_name": "视觉 FAE / 交付工程师",
        "description": "现场问题定位、重复性数据采集与 FAT 验收报告输出",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 20, True),
            ("方案设计", 20, True),
            ("模型测试", 30, True),
            ("实训报告上传", 30, True),
        ),
    },
    {
        "project_name": "视觉检测方案设计与成本测算实训",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024004",
        "job_name": "视觉方案工程师",
        "description": "需求拆解、可行性评估、成像硬件选型与成本 BOM 测算",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 30, True),
            ("方案设计", 40, True),
            ("实训报告上传", 30, True),
        ),
    },
    {
        "project_name": "视觉软件客户端开发实训",
        "project_level": "ADVANCED",
        "difficulty": 4,
        "teacher_no": "T2024004",
        "job_name": "视觉软件工程师",
        "description": "开发图像采集、参数配置、结果展示与报表导出的视觉客户端软件",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 20, True),
            ("方案设计", 25, True),
            ("模型训练", 20, True),
            ("模型测试", 15, True),
            ("实训报告上传", 20, True),
        ),
    },
    {
        "project_name": "整线视觉系统集成联调实训",
        "project_level": "EXPANDED",
        "difficulty": 5,
        "teacher_no": "T2024003",
        "job_name": "视觉系统集成工程师",
        "description": "模型服务化、PLC/机械臂信号联锁与整线联调直至验收",
        "status": "PUBLISHED",
        "modules": (
            ("需求分析", 15, True),
            ("方案设计", 20, True),
            ("模型训练", 15, True),
            ("模型优化", 10, True),
            ("模型测试", 20, True),
            ("实训报告上传", 20, True),
        ),
    },
)

#: 项目 -> 该项目要训练的技能节点：按项目目标从归纳文档 §6 矩阵里挑，
#: 保证 35 个技能点每个都至少被一个已发布项目覆盖（否则技能点进度恒为 0）
PROJECT_SKILLS: dict[str, tuple[str, ...]] = {
    "工业缺陷检测实训": (
        "图像基础",
        "图像滤波",
        "边缘检测",
        "形态学处理",
        "特征提取",
        "光源选型",
        "成像调试",
        "模板匹配与定位",
        "几何测量与标定",
        "视觉软件工具应用",
        "数据与质量分析",
        "技术文档与报告",
    ),
    "表面缺陷分类进阶": (
        "图像滤波",
        "边缘检测",
        "特征提取",
        "深度学习基础",
        "数据标注",
        "模型训练",
        "模型调优",
        "目标检测模型",
        "分割与分类模型",
        "数据与质量分析",
        "技术文档与报告",
    ),
    "成像系统搭建实训": ("光源选型", "镜头选型", "相机选型", "成像调试"),
    "光学成像方案设计与选型实训": (
        "光源选型",
        "镜头选型",
        "相机选型",
        "成像调试",
        "成像方案与可行性评估",
        "复杂成像攻关",
        "视觉软件工具应用",
        "技术文档与报告",
    ),
    "工业质检模型训练与部署实训": (
        "深度学习基础",
        "数据标注",
        "CNN 原理",
        "模型训练",
        "模型调优",
        "目标检测模型",
        "模型轻量化与推理部署",
        "数据与质量分析",
    ),
    "缺陷数据集标注与训练实训": (
        "图像基础",
        "深度学习基础",
        "数据标注",
        "CNN 原理",
        "模型训练",
        "技术文档与报告",
    ),
    "产线视觉系统安装调试实训": (
        "图像基础",
        "形态学处理",
        "成像调试",
        "几何测量与标定",
        "视觉软件工具应用",
        "PLC 与 IO 通讯",
        "现场交付与验收",
    ),
    "客户现场交付与验收实训": (
        "成像方案与可行性评估",
        "工控机与视觉接口",
        "PLC 与 IO 通讯",
        "现场交付与验收",
        "数据与质量分析",
        "技术文档与报告",
    ),
    "视觉检测方案设计与成本测算实训": (
        "光源选型",
        "镜头选型",
        "相机选型",
        "成像方案与可行性评估",
        "几何测量与标定",
        "技术文档与报告",
        "成本与 BOM 测算",
    ),
    "视觉软件客户端开发实训": (
        "图像基础",
        "视觉软件工具应用",
        "模型服务化",
        "Linux 基础",
        "Docker 容器",
        "工控机与视觉接口",
        "开发语言与客户端",
    ),
    "整线视觉系统集成联调实训": (
        "OCR 与条码识别",
        "模型服务化",
        "模型轻量化与推理部署",
        "产线联调",
        "Linux 基础",
        "Docker 容器",
        "工控机与视觉接口",
        "PLC 与 IO 通讯",
        "机械臂与手眼标定",
        "现场交付与验收",
    ),
}

# --------------------------------------------------------------- 填写引导子标题

#: 项目关卡的填写引导子标题：**由教师手填**，模块库不预设。
#: 三个已有项目（§4.1~§4.3）沿用原有内容，示例项目按各自的训练目标编写。
PROJECT_MODULE_ITEMS: dict[tuple[str, str], list[dict[str, str]]] = {
    # ---- 工业缺陷检测实训（机器视觉检测方向）
    ("工业缺陷检测实训", "需求分析"): [
        {"title": "检测对象描述", "prompt": "工件名称、材质、尺寸范围"},
        {"title": "缺陷类型定义", "prompt": "划痕/凹坑/脏污的判定标准"},
        {"title": "检测精度要求", "prompt": "最小可检缺陷尺寸与误检率上限"},
    ],
    ("工业缺陷检测实训", "方案设计"): [
        {"title": "相机选型方案", "prompt": "面阵/线阵选择与分辨率计算"},
        {"title": "光源方案", "prompt": "条光/背光/同轴光的打光对比"},
        {"title": "镜头与视野计算", "prompt": "视野、工作距离与景深核算"},
    ],
    ("工业缺陷检测实训", "数据处理"): [
        {"title": "数据集构建", "prompt": "采集样本数量与正负样本比例"},
        {"title": "缺陷标注规范", "prompt": "标注框规则与边界处理"},
    ],
    ("工业缺陷检测实训", "模型训练"): [
        {"title": "检测模型选型", "prompt": "YOLO / Faster R-CNN 的取舍"},
        {"title": "训练配置与增强", "prompt": "超参、数据增强与训练轮次"},
    ],
    ("工业缺陷检测实训", "模型优化"): [
        {"title": "误检漏检优化", "prompt": "针对的问题与采用的优化手段"},
        {"title": "指标对比", "prompt": "优化前后 mAP 与漏检率对比"},
    ],
    ("工业缺陷检测实训", "模型测试"): [
        {"title": "测试集与指标", "prompt": "测试集构成与评价指标"},
        {"title": "典型错误案例", "prompt": "误检/漏检样本与原因分析"},
    ],
    ("工业缺陷检测实训", "实训报告上传"): [
        {"title": "报告结构与结论", "prompt": "章节安排与主要结论"},
        {"title": "问题与改进", "prompt": "遇到的问题与后续改进方向"},
    ],
    # ---- 表面缺陷分类进阶（深度学习分类方向）
    ("表面缺陷分类进阶", "需求分析"): [
        {"title": "缺陷类别清单", "prompt": "多分类任务的类别与样本量"},
        {"title": "数据规模与来源", "prompt": "采集设备、标注方式与数据分布"},
    ],
    ("表面缺陷分类进阶", "数据处理"): [
        {"title": "类别平衡策略", "prompt": "长尾类别的处理方式"},
        {"title": "数据增强方案", "prompt": "几何与颜色增强的组合"},
    ],
    ("表面缺陷分类进阶", "模型训练"): [
        {"title": "模型选型对比", "prompt": "ResNet / EfficientNet / ViT 的取舍"},
        {"title": "训练超参设置", "prompt": "学习率、batch size 与调度策略"},
        {"title": "训练过程监控", "prompt": "loss 曲线与过拟合判断"},
    ],
    ("表面缺陷分类进阶", "模型优化"): [
        {"title": "过拟合处理", "prompt": "正则化、早停与数据增广"},
        {"title": "分类指标对比", "prompt": "准确率、召回率与混淆矩阵"},
    ],
    ("表面缺陷分类进阶", "实训报告上传"): [
        {"title": "实验记录整理", "prompt": "各组实验配置与结果汇总"},
        {"title": "结论与不足", "prompt": "主要结论与尚存不足"},
    ],
    # ---- 成像系统搭建实训（光学成像方向，草稿）
    ("成像系统搭建实训", "需求分析"): [
        {"title": "检测对象与视野要求", "prompt": "工件尺寸与所需视野范围"},
        {"title": "成像难点分析", "prompt": "反光、曲面、深色等成像难点"},
    ],
    ("成像系统搭建实训", "方案设计"): [
        {"title": "光源方案", "prompt": "均匀性与稳定性验证"},
        {"title": "镜头方案", "prompt": "放大倍率与畸变控制"},
        {"title": "相机方案", "prompt": "接口、带宽与触发方式"},
    ],
    ("成像系统搭建实训", "实训报告上传"): [
        {"title": "选型依据汇总", "prompt": "光源/镜头/相机的选型对比"},
        {"title": "调试记录与结论", "prompt": "成像调试过程与最终结论"},
    ],
    # ---- 光学成像方案设计与选型实训
    ("光学成像方案设计与选型实训", "需求分析"): [
        {"title": "检测对象与精度要求", "prompt": "被测特征的最小尺寸与精度要求"},
        {"title": "成像难点与风险预判", "prompt": "反光、深色、曲面等难点及初步判断"},
    ],
    ("光学成像方案设计与选型实训", "方案设计"): [
        {"title": "可行性评估结论", "prompt": "按定位/测量/识别/检测四类给出的判定"},
        {"title": "光源方案对比", "prompt": "条光/背光/同轴光的打光实验与结论"},
        {"title": "镜头与工作距离核算", "prompt": "焦距、视野、景深的计算过程"},
        {"title": "相机分辨率推导", "prompt": "按像素当量推导所需分辨率与帧率"},
    ],
    ("光学成像方案设计与选型实训", "数据处理"): [
        {"title": "成像实验记录", "prompt": "各轮打光/曝光参数的图像与结论"},
        {"title": "图像质量评估", "prompt": "对比度、均匀性与缺陷信噪比"},
    ],
    ("光学成像方案设计与选型实训", "实训报告上传"): [
        {"title": "选型对比表与结论", "prompt": "光源/镜头/相机的选型依据汇总"},
        {"title": "遗留风险与建议", "prompt": "尚不可检的风险点与替代方案"},
    ],
    # ---- 工业质检模型训练与部署实训
    ("工业质检模型训练与部署实训", "需求分析"): [
        {"title": "检测任务定义", "prompt": "检测/分割/分类任务的界定与判定标准"},
        {"title": "数据规模与标注规范", "prompt": "样本量、类别分布与标注规则"},
    ],
    ("工业质检模型训练与部署实训", "数据处理"): [
        {"title": "数据集划分与增强", "prompt": "训练/验证/测试划分与增强策略"},
        {"title": "类别平衡与清洗", "prompt": "长尾类别处理与异常样本清理"},
    ],
    ("工业质检模型训练与部署实训", "模型训练"): [
        {"title": "模型选型对比", "prompt": "YOLO / Faster R-CNN / 分类网络的取舍"},
        {"title": "训练配置与监控", "prompt": "超参、学习率调度与训练日志"},
        {"title": "训练结果分析", "prompt": "loss 曲线、指标变化与问题定位"},
    ],
    ("工业质检模型训练与部署实训", "模型优化"): [
        {"title": "精度与速度权衡", "prompt": "精度下降可接受范围与加速收益"},
        {"title": "轻量化与推理加速", "prompt": "TensorRT / ONNX 的转换与验证"},
    ],
    ("工业质检模型训练与部署实训", "模型测试"): [
        {"title": "测试集指标与错误分析", "prompt": "mAP/准确率与典型误检漏检样本"},
        {"title": "端侧推理性能验证", "prompt": "单帧耗时、显存占用与并发能力"},
    ],
    ("工业质检模型训练与部署实训", "实训报告上传"): [
        {"title": "训练过程记录", "prompt": "各组实验配置与指标对比"},
        {"title": "结论与部署建议", "prompt": "最终选型、部署环境与注意事项"},
    ],
    # ---- 缺陷数据集标注与训练实训
    ("缺陷数据集标注与训练实训", "需求分析"): [
        {"title": "缺陷界定方式", "prompt": "与客户/现场对齐的判定口径"},
        {"title": "标注规范草案", "prompt": "类别定义、边界规则与存疑处理"},
    ],
    ("缺陷数据集标注与训练实训", "数据处理"): [
        {"title": "数据采集与清洗", "prompt": "采集设备、筛选标准与去重方式"},
        {"title": "标注质量检查", "prompt": "抽检比例、问题类型与返工记录"},
        {"title": "数据集版本与划分", "prompt": "版本管理、划分比例与格式转换"},
    ],
    ("缺陷数据集标注与训练实训", "模型训练"): [
        {"title": "基础训练流程", "prompt": "环境搭建、脚本配置与训练轮次"},
        {"title": "首轮指标与日志", "prompt": "首轮训练指标、日志与问题记录"},
    ],
    ("缺陷数据集标注与训练实训", "实训报告上传"): [
        {"title": "标注规范定稿", "prompt": "规范终版与典型标注示例"},
        {"title": "数据集说明与结论", "prompt": "数据规模、分布与训练结论"},
    ],
    # ---- 产线视觉系统安装调试实训
    ("产线视觉系统安装调试实训", "需求分析"): [
        {"title": "现场检测需求确认", "prompt": "检测项、判定标准与产能要求"},
        {"title": "节拍与安装约束", "prompt": "产线节拍、安装空间与环境干扰"},
    ],
    ("产线视觉系统安装调试实训", "方案设计"): [
        {"title": "视觉工具流程搭建", "prompt": "工具链选择、流程结构与参数设置"},
        {"title": "相机与光源安装方案", "prompt": "安装位置、角度与固定方式"},
        {"title": "配方与参数策略", "prompt": "多型号切换时的配方管理方式"},
    ],
    ("产线视觉系统安装调试实训", "模型测试"): [
        {"title": "现场调试验证", "prompt": "连续运行下的检出效果与稳定性"},
        {"title": "常见异常处理", "prompt": "图像质量、误判与通讯异常的处理记录"},
    ],
    ("产线视觉系统安装调试实训", "实训报告上传"): [
        {"title": "调试记录汇总", "prompt": "调试步骤、问题与解决过程"},
        {"title": "培训与交接材料", "prompt": "客户操作培训要点与维护说明"},
    ],
    # ---- 客户现场交付与验收实训
    ("客户现场交付与验收实训", "需求分析"): [
        {"title": "客户需求与验收指标", "prompt": "漏检率/误检率/节拍等硬指标"},
        {"title": "现场环境与接口确认", "prompt": "供电、网络、PLC 与上位机接口"},
    ],
    ("客户现场交付与验收实训", "方案设计"): [
        {"title": "交付实施方案", "prompt": "实施步骤、人员分工与时间安排"},
        {"title": "风险预案与沟通计划", "prompt": "可能的风险点与应对措施"},
    ],
    ("客户现场交付与验收实训", "模型测试"): [
        {"title": "现场问题定位分析", "prompt": "问题归类（成像/算法/电气/通讯）与初判依据"},
        {"title": "重复性与质量数据", "prompt": "CPK / GR&R / Correlation 数据与结论"},
        {"title": "验收指标对比", "prompt": "实测指标与合同指标的逐项对比"},
    ],
    ("客户现场交付与验收实训", "实训报告上传"): [
        {"title": "FAT 验收报告", "prompt": "验收项、结论与遗留问题"},
        {"title": "问题统计与闭环记录", "prompt": "现场问题清单、责任人与闭环状态"},
    ],
    # ---- 视觉检测方案设计与成本测算实训
    ("视觉检测方案设计与成本测算实训", "需求分析"): [
        {"title": "检测对象与缺陷类型", "prompt": "对象材质、缺陷形态与判定标准"},
        {"title": "节拍与验收指标", "prompt": "产能节拍、精度与漏检率要求"},
        {"title": "产线现状与约束", "prompt": "现有设备、空间与接口条件"},
    ],
    ("视觉检测方案设计与成本测算实训", "方案设计"): [
        {"title": "可行性评估结论", "prompt": "四类场景下的判定结论与依据"},
        {"title": "成像硬件选型对比", "prompt": "候选方案、参数与优缺点对比"},
        {"title": "成本与 BOM 测算", "prompt": "硬件 + 人力 + 其他成本的估算过程"},
    ],
    ("视觉检测方案设计与成本测算实训", "实训报告上传"): [
        {"title": "技术方案书", "prompt": "方案总体结构、拓扑与选型结论"},
        {"title": "汇报材料与风险提示", "prompt": "方案 PPT 要点与需提前预警的风险"},
    ],
    # ---- 视觉软件客户端开发实训
    ("视觉软件客户端开发实训", "需求分析"): [
        {"title": "软件功能清单", "prompt": "界面、参数配置、结果展示与追溯功能"},
        {"title": "硬件接口与通讯需求", "prompt": "相机 SDK、板卡与 PLC 的对接要求"},
    ],
    ("视觉软件客户端开发实训", "方案设计"): [
        {"title": "软件架构与界面设计", "prompt": "模块划分、线程模型与交互流程"},
        {"title": "数据存储与追溯方案", "prompt": "不良图片存储、产量统计与报表导出"},
    ],
    ("视觉软件客户端开发实训", "模型训练"): [
        {"title": "算法模块集成", "prompt": "算法调用方式、参数与结果回传"},
        {"title": "相机 SDK 对接", "prompt": "采集参数、触发方式与带宽验证"},
    ],
    ("视觉软件客户端开发实训", "模型测试"): [
        {"title": "功能与稳定性测试", "prompt": "长时间运行、异常恢复与内存占用"},
        {"title": "配方与参数管理测试", "prompt": "多型号配方切换与误操作防护"},
    ],
    ("视觉软件客户端开发实训", "实训报告上传"): [
        {"title": "软件文档与用户手册", "prompt": "需求/设计/测试文档与操作说明"},
        {"title": "开发总结与改进方向", "prompt": "遇到的问题、折中方案与后续计划"},
    ],
    # ---- 整线视觉系统集成联调实训
    ("整线视觉系统集成联调实训", "需求分析"): [
        {"title": "整线节拍与联调目标", "prompt": "节拍、工位划分与验收目标"},
        {"title": "设备接口清单", "prompt": "相机、工控机、PLC、机械臂的接口与协议"},
    ],
    ("整线视觉系统集成联调实训", "方案设计"): [
        {"title": "系统架构与部署方案", "prompt": "服务部署位置、容器化与资源规划"},
        {"title": "通讯与信号联锁设计", "prompt": "触发拍照、结果回传与分选联锁逻辑"},
    ],
    ("整线视觉系统集成联调实训", "模型训练"): [
        {"title": "模型服务封装", "prompt": "推理服务的接口设计与调用方式"},
        {"title": "服务性能与并发验证", "prompt": "吞吐、时延与并发下的稳定性"},
    ],
    ("整线视觉系统集成联调实训", "模型优化"): [
        {"title": "推理加速与资源优化", "prompt": "批处理、量化与显存占用优化"},
        {"title": "稳定性与异常恢复", "prompt": "重启策略、超时处理与降级方案"},
    ],
    ("整线视觉系统集成联调实训", "模型测试"): [
        {"title": "整线联调记录", "prompt": "联调步骤、参与方与结果"},
        {"title": "异常排查记录", "prompt": "信号不同步、节拍卡顿等问题的排查过程"},
    ],
    ("整线视觉系统集成联调实训", "实训报告上传"): [
        {"title": "部署文档与调试 SOP", "prompt": "部署步骤、参数与排查手册"},
        {"title": "验收记录与交接材料", "prompt": "验收结论、遗留问题与交接清单"},
    ],
}


# ------------------------------------------------------------------- 写入逻辑


async def _first_teacher_id(session: AsyncSession) -> int | None:
    """库里的第一个教师账号 ID（没有教师时返回 None，项目创建人可空）。"""
    stmt = select(SysUser).where(SysUser.user_type == "TEACHER").order_by(SysUser.id).limit(1)
    teacher = (await session.exec(stmt)).first()
    return int(teacher.id) if teacher is not None else None


async def run_growth_seed(
    session: AsyncSession | None = None,
    *,
    teacher_ids: dict[str, int] | None = None,
) -> dict[str, int]:
    """写入技能体系 / 技能点 / 岗位 / 岗位技能 / 项目 / 项目技能，返回各类新增数量。

    幂等：全部按名称查重，已存在的不重复插入。技能进度不在这里算 —— 项目发布状态或
    关联技能变化后，调用方按需用 ``app.services.skill`` 重算。

    ``teacher_ids`` 是可选的「工号 -> 用户 ID」映射，用于给项目填创建人；
    不传时退化为库里第一个教师账号（没有教师就留空）。
    """
    own_session = session is None
    session = session or SessionLocal()
    stats = {
        "skill_trees": 0,
        "skill_nodes": 0,
        "jobs": 0,
        "job_skills": 0,
        "projects": 0,
        "project_modules": 0,
        "project_skills": 0,
    }

    try:
        trees = SkillTreeRepository(session)
        nodes = SkillNodeRepository(session)
        jobs = JobRepository(session)
        job_skills = JobSkillRepository(session)
        templates = StageTemplateRepository(session)
        projects = TrainingProjectRepository(session)
        project_modules = ProjectModuleRepository(session)
        project_skills = ProjectSkillRepository(session)

        # ------------------------------------------------------------ 技能体系
        tree_ids: dict[str, int] = {}
        for item in SKILL_TREES:
            tree = await trees.by_name(item["tree_name"])
            if tree is None:
                tree = await trees.create(
                    {"tree_name": item["tree_name"], "description": item["description"]}
                )
                stats["skill_trees"] += 1
            tree_ids[item["tree_name"]] = int(tree.id)

        # ------------------------------------------------------------ 技能节点
        node_ids: dict[str, int] = {}
        for tree_name, node_items in SKILL_NODES.items():
            tree_id = tree_ids[tree_name]
            for node_name, node_desc in node_items:
                node = await nodes.by_name(node_name)
                if node is None:
                    node = await nodes.create(
                        {"tree_id": tree_id, "node_name": node_name, "description": node_desc}
                    )
                    stats["skill_nodes"] += 1
                node_ids[node_name] = int(node.id)

        # -------------------------------------------------------- 岗位与岗位技能
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
            job_ids[item["job_name"]] = int(job.id)

            linked = {int(node.id) for node in await job_skills.list_nodes_of_job(job.id)}
            for node_name in item["skill_names"]:
                node_id = node_ids[node_name]
                if node_id in linked:
                    continue
                await job_skills.add_skill(job.id, node_id)
                linked.add(node_id)
                stats["job_skills"] += 1

        # ------------------------------------------------- 项目 + 关卡 + 项目技能
        fallback_teacher_id = await _first_teacher_id(session)
        project_ids: dict[str, int] = {}
        for item in PROJECTS:
            creator_id = None
            if teacher_ids:
                creator_id = teacher_ids.get(item.get("teacher_no") or "")
            creator_id = creator_id or fallback_teacher_id

            project = await projects.by_name(item["project_name"])
            if project is None:
                project = await projects.create(
                    {
                        "project_name": item["project_name"],
                        "project_level": item["project_level"],
                        "difficulty": item["difficulty"],
                        "job_id": job_ids[item["job_name"]],
                        "description": item["description"],
                        "status": item["status"],
                        "creator_id": creator_id,
                    }
                )
                stats["projects"] += 1
            elif project.creator_id is None and creator_id is not None:
                # 基础种子先建过项目（那时还没有教师账号），演示数据补上创建人
                await projects.update(project, {"creator_id": creator_id})
            project_ids[item["project_name"]] = int(project.id)

            for stage_no, (stage_name, weight, required) in enumerate(item["modules"], start=1):
                template = await templates.by_name(stage_name)
                if template is None:
                    raise SystemExit(
                        f"模块库里没有「{stage_name}」，请先跑 python -m app.db.init_db 建标准模块库"
                    )
                planned_items = PROJECT_MODULE_ITEMS.get((item["project_name"], stage_name), [])
                exists = await project_modules.by_template(project.id, template.id)
                if exists is not None:
                    # 底稿为准：子标题对不上就同步（只影响这些演示/示例项目）
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

            linked_nodes = {int(node.id) for node in await project_skills.list_nodes_of_project(project.id)}
            for node_name in PROJECT_SKILLS.get(item["project_name"], ()):
                node_id = node_ids[node_name]
                if node_id in linked_nodes:
                    continue
                await project_skills.add_skill(project.id, node_id)
                linked_nodes.add(node_id)
                stats["project_skills"] += 1

        await session.commit()
    finally:
        if own_session:
            await session.close()

    return stats


if __name__ == "__main__":
    import asyncio

    from app.core.db import engine

    async def _main() -> None:
        try:
            print(await run_growth_seed())
        finally:
            await engine.dispose()

    asyncio.run(_main())


__all__ = [
    "JOBS",
    "PROJECTS",
    "PROJECT_MODULE_ITEMS",
    "PROJECT_SKILLS",
    "SKILL_NODES",
    "SKILL_TREES",
    "run_growth_seed",
]
