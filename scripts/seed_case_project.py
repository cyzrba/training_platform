"""按《工业视觉检测项目交付流程——三端稳压管案例版》造一套完整的实训数据并跑通 AI 评审。

一次执行会完整走一遍真实业务链路：

    建项目（七个标准模块 + 每个模块的引导子标题）
      → 关联项目所需技能点（评审通过后技能进度按它推进）
      → 上传报告模板与评分标准（评分标准自动入库 + 向量化）
      → 建学生 → 入班 → 选岗（并把项目技能点补进该岗位）
      → 开始闯关 → 逐关卡按子标题填写作答
      → 把写好的实训报告作为附件挂到"实训报告上传"关卡 → 整单提交
      → 触发 AI 评审（RAG 召回本项目的评分标准 → DeepSeek 打分 → 落库结算）

为什么要连班级和岗位一起造：审核列表的"班级"列、数据总览的"在读学生 / 岗位热度"、
学习过程统计的"班级 / 岗位"、技能树与技能进度**都挂在这两条关联上**。只造项目与作答，
这些页面就是空的；技能进度更是完全不动（`project_skill` 是技能推进的唯一入口）。

用法：
    uv run python scripts/seed_case_project.py                 # 全流程（会真实调用大模型）
    uv run python scripts/seed_case_project.py --no-review     # 只造数据，不跑 AI 评审
    uv run python scripts/seed_case_project.py --user-no 2026999

前置：MinIO 已启动（存储层只有 S3 后端）；要跑 AI 评审还需要 Milvus + rag 依赖 + 模型权重，
以及 ``system_config`` 里配好 ``ai.llm.api_key``。

关于图片：本案例的判定依据是引脚图像，但**本脚本只造文字数据**——学生作答与实训报告
都是纯文本，不涉及图片附件。图片类附件会被解析器判为"无法解析"并如实告知模型。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.db import get_db as real_get_db  # noqa: E402
from app.main import app  # noqa: E402

PROJECT_NAME = "工业视觉检测项目·TO-220 三端稳压管引脚缺陷检测"

#: 案例学生要落的班级（不存在则创建）：做报表、看板、导出都按班级聚合
CLASS_NAME = "人工智能2401班"

#: 案例学生要选的岗位（不存在则创建）
JOB_NAME = "工业视觉工程师"

#: 本项目要推进的技能点：(技能树 code, 技能树名, 技能节点 code, 技能节点名)
#: 照案例的交付环节挑的：光学选型 → 图像处理 → 模型训练调优 → 产线联调。
#: 节点缺失会在对应技能树下补建，所以脚本不依赖种子数据是否跑过。
PROJECT_SKILL_NODES: list[tuple[str, str, str, str]] = [
    ("OPTICAL_IMAGING", "光学成像系", "LIGHT_SELECT", "光源选型"),
    ("OPTICAL_IMAGING", "光学成像系", "CAMERA_SELECT", "相机选型"),
    ("OPTICAL_IMAGING", "光学成像系", "PIN_DEFECT", "引脚缺陷检测"),
    ("TRADITIONAL_ALGORITHM", "传统算法系", "IMG_FILTER", "图像滤波"),
    ("TRADITIONAL_ALGORITHM", "传统算法系", "EDGE_DETECT", "边缘检测"),
    ("DEEP_LEARNING", "深度学习系", "MODEL_TRAIN", "模型训练"),
    ("DEEP_LEARNING", "深度学习系", "MODEL_TUNE", "模型调优"),
    ("SYSTEM_DEPLOYMENT", "系统部署系", "PRODUCTION_DEBUG", "产线联调"),
]

# --------------------------------------------------------------- 七个模块的引导子标题

STAGE_GUIDES: dict[str, list[str]] = {
    "REQUIREMENT_ANALYSIS": [
        "检测对象与封装规格",
        "缺陷类型与量化判定标准",
        "节拍与产能约束",
        "精度与检出率指标",
        "现场环境与产线对接",
        "需求确认与风险提示",
    ],
    "SOLUTION_DESIGN": [
        "可行性评估结论",
        "相机选型与分辨率推导",
        "镜头选型与工作距离",
        "光源与打光方式",
        "算法路线选型",
        "软件功能设计",
    ],
    "DATA_PROCESSING": [
        "样品收集与分类",
        "数据集划分",
        "标注规范",
        "数据增强策略",
        "数据版本管理",
    ],
    "MODEL_TRAINING": [
        "模型结构设计",
        "训练环境与超参",
        "训练过程与收敛",
        "指标监控",
    ],
    "MODEL_OPTIMIZATION": [
        "推理耗时优化",
        "漏检与误检优化",
        "模型轻量化",
        "阈值与后处理调优",
    ],
    "MODEL_TESTING": [
        "测试集与测试方法",
        "检出能力（召回）",
        "抗干扰能力（精确率）",
        "节拍与稳定性",
        "遗留风险",
    ],
    "REPORT_UPLOAD": [
        "报告文件",
        "关键结论摘要",
    ],
}

# --------------------------------------------------------------- 评分标准（EVAL_CRITERIA）

CRITERIA_MD = """# 工业视觉检测项目评分标准

总分 100 分，按七个关卡权重分配。评分依据是各关卡作答与上传的实训报告，
要点齐全、数据可核对、结论有据者得高分；只写口号、缺量化指标的按缺项扣分。

## 一、需求分析（10 分）

必须覆盖六项：检测对象与封装规格、缺陷类型与量化判定标准、节拍与产能约束、
精度与检出率指标、现场环境与产线对接、需求确认与风险提示。
每缺一项扣 1.5 分；判定标准没有量化数值（角度、偏移量、漏检率）的本项最高 5 分；
未指出需求中相互冲突或不可达之处的扣 2 分。

## 二、方案设计（15 分）

必须给出可行性评估结论、相机选型（含分辨率推导）、镜头选型（含工作距离）、
光源与打光方式、算法路线选型依据、软件功能设计。
每缺一项扣 2 分；选型没有推导过程、只给结论的每项扣 1 分；
算法路线没有说明"为什么选传统/深度学习"的扣 2 分。

## 三、数据处理（15 分）

必须说明样品收集与分类、数据集划分比例、标注规范、数据增强策略、数据版本管理。
每缺一项扣 2.5 分；未说明数据集划分比例的扣 4 分；未说明标注规范的扣 3 分；
没有交代样本量是否满足训练需求的扣 2 分。

## 四、模型训练（20 分）

必须说明模型结构设计、训练环境与超参、训练过程与收敛情况、指标监控方式。
每缺一项扣 4 分；超参只写"默认值"而没有具体数值的扣 2 分；
没有给出训练过程中任何量化指标（loss、mAP、准确率）的扣 4 分。

## 五、模型优化（15 分）

必须说明推理耗时优化、漏检与误检优化、模型轻量化、阈值与后处理调优。
每缺一项扣 3 分；优化前后没有对比数据的每项扣 1.5 分；
只调阈值而不分析误检来源的扣 2 分。

## 六、模型测试（15 分）

必须说明测试集与测试方法、检出能力（召回率）、抗干扰能力（精确率）、
节拍与稳定性、遗留风险。
每缺一项扣 2.5 分；把训练集或验证集当测试集用的扣 5 分；
未给出召回率与精确率具体数值的每项扣 2 分。

## 七、实训报告（10 分）

报告需结构完整（项目背景、需求、方案、数据、训练、优化、测试、结论），
数据与前面各关卡一致，结论可复核。缺章节每处扣 1 分；
与前文数据不一致的每处扣 2 分；结论没有量化支撑的扣 2 分；
未附报告文件的，本项 0 分。
"""

# --------------------------------------------------------------- 项目附件：报告模板

REPORT_TEMPLATE_MD = """# 视觉检测项目实训报告（模板）

## 封面
项目名称：／学生姓名：／学号：／提交日期：

## 1 项目背景与检测对象
说明产线现状、检测对象、封装与规格、不良造成的后果。

## 2 需求分析
逐条列出检测对象、缺陷类型、量化判定标准、节拍、精度与检出率指标、
现场环境与产线对接要求。

## 3 可行性评估
按定位／测量／识别／检测四类给结论，说明精度反推过程与风险等级。

## 4 成像硬件方案
相机分辨率推导、镜头焦距与工作距离、光源选型、安装方案。
**必须给出推导过程与最终型号，不能只写结论。**

## 5 算法设计
传统算法与深度学习的选型依据、模型结构、输入输出定义。

## 6 数据处理
样品来源与分类、数据集划分比例、标注规范、数据增强策略。

## 7 模型训练与优化
训练环境、超参、收敛过程；推理耗时、漏检误检的优化措施与前后对比。

## 8 模型测试
测试集构成、召回率、精确率、节拍与稳定性、遗留风险。

## 9 结论与改进方向
量化结论 + 已知局限 + 下一步计划。

> 提交要求：报告正文以文字与数据为主，图表用文字描述代替；随报告一并上传本文件。
"""

# --------------------------------------------------------------- 学生作答（按子标题逐项）

ANSWERS: dict[str, list[tuple[str, str]]] = {
    "REQUIREMENT_ANALYSIS": [
        (
            "检测对象与封装规格",
            "TO-220 直插封装三端稳压器，3 根引出引脚，封装顶部带 Φ4.5mm 散热固定安装圆孔。"
            "散装来料，需配置上料定位机构保证拍摄姿态统一。检测工位设在来料上料后、插件工序前，在线全检。",
        ),
        (
            "缺陷类型与量化判定标准",
            "分三类判定：\n"
            "1) 合格品 OK——三根引脚笔直、长短一致、间距均匀无变形；\n"
            "2) 歪脚 NG1——任意引脚倾斜角 >3°，或引脚水平位置偏移 >±0.07mm，满足任一即判不良；\n"
            "3) 断脚 NG2——图像内引脚轮廓缺失、引脚数量不足 3 根，即判不良。\n"
            "三类需分三个料道收纳，不可混料。",
        ),
        (
            "节拍与产能约束",
            "客户原始要求节拍匹配现有产线 400PCS。细化后：单颗物料单次拍照检测耗时 ≤150ms，"
            "支持最高 600PCS/分钟的产线节拍，不能成为产线瓶颈。"
            "按工程惯例把图像采集+传输控制在总节拍的 40% 以内，即 ≤60ms。",
        ),
        (
            "精度与检出率指标",
            "缺陷漏检率 ≤0.03%，整体误检率 ≤0.1%。"
            "引脚间距偏差 ≤0.07mm，引脚倾斜角 >3° 判不良；"
            "系统最小可识别精度需优于 70μm 才能稳定判定 0.07mm 的偏移量。",
        ),
        (
            "现场环境与产线对接",
            "需输出 OK、NG1-歪脚、NG2-断脚 三类开关量信号对接分选机构。"
            "所有 NG 产品自动保存原图并按缺陷类型分文件夹存储，本地至少保留 30 天。"
            "支持 OK/NG1/NG2 三类产量实时统计并导出 Excel。现场为室内产线，"
            "需评估粉尘与昼夜光照变化对成像稳定性的影响。",
        ),
        (
            "需求确认与风险提示",
            "两处风险必须在需求确认单上书面明确：\n"
            "1) 客户要求“缺陷不能漏检”，而 ≤0.03% 的漏检率在设计上属极高标准，需以样品实测数据确认可达性；\n"
            "2) 客户仅提供 3 张实拍样品图，远低于 OK 样品 ≥50 个、NG 样品 ≥30 个的基线，"
            "需补采样品或使用缺陷生成算法补足，否则模型泛化能力无法保证。",
        ),
    ],
    "SOLUTION_DESIGN": [
        (
            "可行性评估结论",
            "本项目属检测类。客户明确“漏检优先”，与 ≤0.03% 漏检目标一致。"
            "缺陷为引脚轮廓缺失与位置偏移，属几何特征，灰度差与缺陷面积均满足"
            "“灰度差 ≥20、面积 ≥4×4 像素”的通用基线。"
            "可行性判定为 B 级：大部分可检测，部分极端工况可能漏检或误检，需实测验证。",
        ),
        (
            "相机选型与分辨率推导",
            "视野 FOV = 50mm × 50mm，设计识别精度 70μm，算法最小识别单元 2×2 像素。\n"
            "像素当量上限 = 0.07mm ÷ 2 = 0.035mm/pixel；\n"
            "水平方向最少像素 = 50 ÷ 0.035 ≈ 1429，叠加 10% 工程安全余量后需 ≥1572 像素。\n"
            "结论：选 2448×2048（500 万像素）千兆网口面阵相机，像元尺寸 3.45μm。\n"
            "帧率：采集+传输 ≤60ms，理论最低 ≈17fps；考虑大分辨率图像传输抖动，"
            "预留 30% 安全系数后 ≥22fps，选 30fps 相机（单帧采集传输约 33.3ms）。",
        ),
        (
            "镜头选型与工作距离",
            "按几何相似关系由视野与像元反推焦距，理论值约 56mm，向下取标准定焦工业镜头 50mm"
            "（型号 MVL-MF5028M-5MPE）。对应工作距离 WD ≈ 354mm 时可完整覆盖 50mm 视野。\n"
            "精度校验：算法最小识别单元 2×2 像素 × 像元 3.45μm ≈ 6.9μm 的理论分辨率，"
            "实际成像条件下系统综合精度 ≤50μm，优于设计要求的 70μm，满足检测需求。",
        ),
        (
            "光源与打光方式",
            "采用白色背光源打光，获取元器件轮廓二值化图像，消除表面色差与反光干扰，"
            "精准提取引脚边缘。视野 50×50mm，光源尺寸选 120×120mm 以保证照明均匀覆盖。",
        ),
        (
            "算法路线选型",
            "采用“检测模型 + 分割模型”串联的深度学习方案：轻量化检测模型快速定位引脚特征，"
            "高精度分割模型提取引脚轮廓，再由分类头输出 OK / NG1 / NG2。\n"
            "选型依据：引脚形态虽规则，但断脚、歪脚形态多样且要求 0 漏检——"
            "传统算法数据少、规则明确但复杂缺陷弱、抗干扰差；深度学习对复杂形态与环境变化更稳，"
            "且本项目样本可通过补采与生成补足，数据量不构成瓶颈。",
        ),
        (
            "软件功能设计",
            "支持标准样品一键标定模板与多组产品配方保存调用；运行日志查看；"
            "OK/NG1/NG2 三类产量实时统计并可导出 Excel；历史不良图片检索与回放；"
            "NG 原图按缺陷类型自动分类存储 ≥30 天；输出三类开关量信号对接分选机构。",
        ),
    ],
    "DATA_PROCESSING": [
        (
            "样品收集与分类",
            "客户仅提供 3 张实拍样品图，远低于 OK ≥50、NG ≥30 的基线，故补采："
            "从产线抽取合格品 60 只、歪脚不良 35 只、断脚不良 35 只，共 130 只实拍样本；"
            "不足部分用缺陷生成算法补足至每类 50 只，最终构建 150 个样品的数据集。",
        ),
        (
            "数据集划分",
            "按 6:2:2 分层抽样划分：训练集 90、验证集 30、测试集 30，"
            "保证 OK / 歪脚 / 断脚 三类在三个子集中的比例一致。"
            "测试集自划分起即隔离，训练与调参全过程不参与。",
        ),
        (
            "标注规范",
            "引脚区域采用多边形分割标注，类别标签为 OK、NG_BEND（歪脚）、NG_BREAK（断脚）；"
            "歪脚样本额外标注倾斜角与偏移量。标注由两名工程师交叉完成，分歧由组长仲裁，"
            "每张图二次复核，标注一致率要求 ≥98%。",
        ),
        (
            "数据增强策略",
            "训练集在线增强：随机旋转 ±5°、亮度抖动 ±15%、高斯噪声 σ=2、随机遮挡。"
            "验证集与测试集不做任何增强，避免评估结果失真。",
        ),
        (
            "数据版本管理",
            "数据集冻结为 v1.0，图像清单与标注文件做哈希校验，训练配置与数据集版本一一对应，保证实验可复现。",
        ),
    ],
    "MODEL_TRAINING": [
        (
            "模型结构设计",
            "两级串联：第一级轻量化目标检测网络定位三根引脚的包围框；"
            "第二级分割模型对引脚区域做像素级分割得到轮廓；"
            "最后由分类头依据轮廓完整性与位置关系输出 OK / NG1 / NG2。",
        ),
        (
            "训练环境与超参",
            "硬件 RTX3090，框架 PyTorch。优化器 SGD，初始学习率 0.01，余弦退火调度，"
            "batch size 16，输入分辨率 640×640，训练 300 epoch，训练集启用在线增强。",
        ),
        (
            "训练过程与收敛",
            "训练 loss 在前 50 epoch 快速下降，150 epoch 后趋于平稳；"
            "验证集 mAP 在 200 epoch 达到 0.98 后不再明显提升，据此在 300 epoch 停止训练，"
            "未观察到明显过拟合。",
        ),
        (
            "指标监控",
            "每 10 epoch 在验证集上评估 mAP、引脚分割 IoU（最终 0.95）与三类分类准确率；"
            "同时记录单张推理耗时，提前发现节拍风险。",
        ),
    ],
    "MODEL_OPTIMIZATION": [
        (
            "推理耗时优化",
            "原模型单张推理 38ms，占用预算偏高。采用 TensorRT FP16 量化与算子融合后降至 21ms；"
            "配合单帧采集传输 33.3ms，单颗总耗时约 90ms，满足 CT ≤150ms 的约束。",
        ),
        (
            "漏检与误检优化",
            "误检来源统计显示，主要是背光下引脚边缘反光导致轮廓断裂被误判为断脚；"
            "通过补充反光样本并调整分割阈值，误检率由 1.2% 降至 0.05%。"
            "漏检方面补充了短脚、根部折断等难例样本。",
        ),
        (
            "模型轻量化",
            "用深度可分离卷积替换主干中的标准卷积，参数量减少约 40%，"
            "mAP 由 0.98 微降至 0.975，精度损失可接受。",
        ),
        (
            "阈值与后处理调优",
            "按“漏检优先”原则，分类置信度阈值由 0.5 下调至 0.35 以保召回；"
            "对分割结果做形态学闭运算填补边缘断点；"
            "增加规则兜底：引脚数量不足 3 根直接判断脚。",
        ),
    ],
    "MODEL_TESTING": [
        (
            "测试集与测试方法",
            "使用隔离的测试集 30 个样品（OK 10 / 歪脚 10 / 断脚 10），"
            "该测试集在训练与调参全过程中从未参与。测试按产线实际节拍连续运行 3 轮取平均。",
        ),
        (
            "检出能力（召回）",
            "歪脚召回率 100%（10/10），断脚召回率 100%（10/10），总体召回率 100%，"
            "优于 ≤0.03% 漏检率的指标要求。",
        ),
        (
            "抗干扰能力（精确率）",
            "合格品误判 0 例，误检率 0%，优于 ≤0.1% 的指标要求；在全部 150 个样品上取得 0 漏检与 0 误检。",
        ),
        (
            "节拍与稳定性",
            "单张推理 21ms，含采集传输后单颗总耗时约 90ms，满足 CT ≤150ms；"
            "连续运行 3 轮无异常退出，三轮结果完全一致。",
        ),
        (
            "遗留风险",
            "1) 测试集样本量偏小（30 个），统计置信度有限；\n"
            "2) 未覆盖强粉尘与夜班无照明等恶况场景；\n"
            "3) 未做 GRR 与 Correlation 验收，本项目虽属检测类不强制，但建议补做以增强说服力；\n"
            "4) 光照老化后的长期稳定性缺少数据支撑。",
        ),
    ],
    "REPORT_UPLOAD": [
        (
            "报告文件",
            "按项目报告模板编写，正文见附件《三端稳压管引脚缺陷检测实训报告.md》。",
        ),
        (
            "关键结论摘要",
            "需求 6 项已逐条确认，判定标准量化到角度（>3°）、偏移（>±0.07mm）与漏检率（≤0.03%）；"
            "成像选型为 2448×2048 面阵相机 + 50mm 定焦镜头 + 120×120 白色背光源，工作距离约 354mm；"
            "算法采用检测与分割模型串联，测试集 30 个样品上取得 0 漏检 0 误检，单张推理 21ms；"
            "已知局限：测试样本量偏小、未覆盖恶况环境、未做 GRR/Correlation 验收。",
        ),
    ],
}

# --------------------------------------------------------------- 学生上传的实训报告（纯文本）

REPORT_MD = """# 三端稳压管引脚缺陷检测实训报告

## 1 项目背景与检测对象

本厂来料为 TO-220 封装三端稳压器直插元器件，在插件工序中多次因引脚不良导致
插件机卡料、虚焊、焊接不良与批量返工。因此需上线机器视觉在线检测设备，对来料引脚做全检。

检测对象：TO-220 直插封装三端稳压器，3 根引出引脚，封装顶部带 Φ4.5mm 散热固定安装圆孔。
检测工位：来料上料后、插件工序前。

## 2 需求分析

| 维度 | 结论 |
| --- | --- |
| 检测对象 | TO-220 三端稳压器，3 引脚，散装来料，需上料定位机构 |
| 缺陷类型 | 歪脚（NG1）、断脚（NG2）、合格（OK）三类 |
| 判定标准 | 歪脚：倾斜角 >3° 或偏移 >±0.07mm；断脚：引脚轮廓缺失或数量 <3 根 |
| 节拍 | 单颗拍照检测 ≤150ms，支持 600PCS/分钟 |
| 指标 | 漏检率 ≤0.03%，误检率 ≤0.1% |
| 对接 | OK/NG1/NG2 三类开关量信号；NG 原图分类存储 ≥30 天 |

风险提示：客户要求“缺陷不能漏检”，而 ≤0.03% 属于工程极高标准，需实测确认；
客户仅提供 3 张样品图，低于 OK ≥50 / NG ≥30 的基线，需补采。

## 3 可行性评估

属检测类项目，客户明确“漏检优先”。缺陷为几何特征，满足“灰度差 ≥20、
面积 ≥4×4 像素”的通用基线。可行性判定 B 级：大部分可检测，需实测验证极端工况。

## 4 成像硬件方案

**像素当量反推**：设计精度 70μm，算法最小识别单元 2×2 像素
→ 像素当量上限 0.035mm/pixel → 50mm 视野所需水平像素 ≈1429，
叠加 10% 余量需 ≥1572 像素。

**选型结论**：

- 相机：2448×2048（500 万像素）千兆网口面阵，像元 3.45μm；
- 帧率：采集+传输 ≤60ms → 理论 ≥17fps，叠加 30% 余量 → ≥22fps，选 30fps；
- 镜头：理论焦距 ≈56mm，向下取 50mm 定焦（MVL-MF5028M-5MPE）；
- 工作距离：约 354mm 可完整覆盖 50×50mm 视野；
- 光源：白色背光，视轮廓二值化需求选择 120×120mm；
- 精度校验：系统综合精度 ≤50μm，优于要求的 70μm。

## 5 算法设计

采用“检测模型 + 分割模型”串联：轻量化检测模型定位引脚，分割模型提取轮廓，
分类头输出三类结果。相比传统算法，深度学习对复杂缺陷形态与环境变化更稳定，
契合本项目 0 漏检的目标。

## 6 数据处理

- 样品：客户 3 张图 + 产线补采 130 只（OK 60 / 歪脚 35 / 断脚 35），缺陷生成补足至每类 50，共 150；
- 划分：6:2:2 分层抽样，训练 90 / 验证 30 / 测试 30，测试集全程隔离；
- 标注：多边形分割标注，两类不良标签，双人交叉标注 + 仲裁，一致率 ≥98%；
- 增强：旋转 ±5°、亮度 ±15%、高斯噪声 σ=2、随机遮挡，仅训练集在线增强；
- 版本：数据集 v1.0 冻结，清单哈希校验。

## 7 模型训练与优化

训练环境 RTX3090 + PyTorch；SGD，lr=0.01 余弦退火，batch 16，640×640，300 epoch。
验证集 mAP 于 200 epoch 达 0.98 后趋于平稳，据此停训。

优化措施与前后对比：

| 项目 | 优化前 | 优化后 |
| --- | --- | --- |
| 单张推理耗时 | 38ms | 21ms（TensorRT FP16 + 算子融合） |
| 误检率 | 1.2% | 0.05%（补反光样本 + 分割阈值调整） |
| 参数量 | 基线 | 减少约 40%（深度可分离卷积） |
| mAP | 0.98 | 0.975 |

阈值策略按“漏检优先”，置信度阈值由 0.5 下调至 0.35，并增加“引脚数 <3 判断脚”的规则兜底。

## 8 模型测试

测试集 30 个样品（OK 10 / 歪脚 10 / 断脚 10），连续运行 3 轮：

- 召回率：歪脚 100%、断脚 100%，总体 100%（优于 ≤0.03% 漏检要求）；
- 精确率：误检 0 例，误检率 0%（优于 ≤0.1% 要求）；
- 节拍：单张 21ms，单颗总耗时约 90ms，满足 CT ≤150ms；
- 稳定性：三轮结果一致，无异常退出。

## 9 结论与改进方向

本方案在测试集上达成 0 漏检 0 误检，节拍与精度指标均满足需求，具备上线条件。

已知局限与下一步：

1. 测试样本量偏小（30 个），需扩大到 ≥100 个以提升统计置信度；
2. 未覆盖强粉尘、夜班无照明等恶况，建议补做环境应力测试；
3. 未做 GRR 与 Correlation 验收，建议补做；
4. 需补充光源老化后的长期稳定性数据。
"""

# --------------------------------------------------------------- 驱动流程


async def call(client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> object:
    """调接口并解开统一响应体；业务失败（code != 200）直接抛出，避免「静默半成功」。"""
    response = await getattr(client, method)(url, **kwargs)
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 200):
        raise SystemExit(f"{method.upper()} {url} 失败：{payload.get('msg')}")
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def render_answer(sections: list[tuple[str, str]]) -> str:
    """按引导子标题渲染作答：子标题原样保留，内容逐项对应，便于评审逐项对照。"""
    return "\n\n".join(f"### {title}\n{body}" for title, body in sections)


async def _page_of(client: httpx.AsyncClient, path: str, **params: object) -> list[dict]:
    """取分页接口的一页数据（造数脚本用大 page_size，不关心翻页）。"""
    page = await call(client, "get", path, params={"page_size": 200, **params})
    return list(page["items"])  # type: ignore[index]


async def _ensure_class(client: httpx.AsyncClient, *, class_name: str) -> dict:
    """按名字找班级，没有就建一个（幂等）。"""
    for item in await _page_of(client, "/api/classes", keyword=class_name):
        if item["class_name"] == class_name:
            return item
    return await call(
        client,
        "post",
        "/api/classes",
        json={"class_name": class_name, "remark": "案例数据脚本创建"},
    )


async def _ensure_job(client: httpx.AsyncClient, *, job_name: str) -> dict:
    """按名字找岗位，没有就建一个（幂等）。"""
    for item in await _page_of(client, "/api/jobs", keyword=job_name):
        if item["job_name"] == job_name:
            return item
    return await call(
        client,
        "post",
        "/api/jobs",
        json={
            "job_name": job_name,
            "direction_tag": "机器视觉",
            "scene": "工业视觉检测项目交付",
            "description": "面向来料视觉检测项目的交付岗位，覆盖光学选型、算法与产线联调",
        },
    )


async def _ensure_skill_nodes(client: httpx.AsyncClient) -> list[dict]:
    """按 code 找技能节点；技能树或节点缺失就补建，返回节点列表。"""
    trees = {item["tree_code"]: item for item in await _page_of(client, "/api/skill-trees")}
    nodes: list[dict] = []
    for tree_code, tree_name, node_code, node_name in PROJECT_SKILL_NODES:
        tree = trees.get(tree_code)
        if tree is None:
            tree = await call(
                client,
                "post",
                "/api/skill-trees",
                json={"tree_code": tree_code, "tree_name": tree_name},
            )
            trees[tree_code] = tree
        siblings = await call(client, "get", f"/api/skill-trees/{tree['id']}/nodes")
        node = next((item for item in siblings if item["node_code"] == node_code), None)
        if node is None:
            node = await call(
                client,
                "post",
                f"/api/skill-trees/{tree['id']}/nodes",
                json={"node_code": node_code, "node_name": node_name},
            )
        nodes.append(node)
    return nodes


async def _ensure_student_role(client: httpx.AsyncClient, *, user_id: int) -> bool:
    """给新建的学生账号补上 STUDENT 角色。

    ``POST /api/users`` 只建账号、不挂角色，而平台是按角色控制菜单与权限的
    （教师端 / 学生端的分界就在这）。这个接口是**覆盖式**的，所以先把已有角色读出来再合并，
    免得重复执行时把别人手工加的角色冲掉。返回是否真的补了。
    """
    student_role = next(
        (item for item in await _page_of(client, "/api/roles") if item["role_code"] == "STUDENT"),
        None,
    )
    if student_role is None:
        return False
    current = {item["id"] for item in await call(client, "get", f"/api/users/{user_id}/roles")}
    if student_role["id"] in current:
        return False
    await call(
        client,
        "post",
        f"/api/users/{user_id}/roles",
        json={"role_ids": sorted(current | {student_role["id"]})},
    )
    return True


async def run(*, user_no: str, project_name: str, run_review: bool) -> None:
    async with SessionLocal() as session:

        async def _override():  # noqa: ANN202
            """复刻生产版 get_db 的事务边界：**请求成功即提交**。

            只 yield 不 commit 的话，所有写入在 session 关闭时会被回滚——
            接口调用看起来全成功、返回值也对，但数据并没有落库。
            """
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        app.dependency_overrides[real_get_db] = _override
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seed", timeout=600) as client:
            await _seed(client, user_no=user_no, project_name=project_name, run_review=run_review)
        app.dependency_overrides.clear()


async def _seed(client: httpx.AsyncClient, *, user_no: str, project_name: str, run_review: bool) -> None:
    templates = {
        item["stage_key"]: item for item in (await call(client, "get", "/api/stage-templates"))["items"]
    }
    missing = [key for key in STAGE_GUIDES if key not in templates]
    if missing:
        raise SystemExit(f"模块库里缺这些关卡：{missing}，先跑 python -m app.db.init_db")

    project = await call(
        client,
        "post",
        "/api/projects",
        json={
            "project_name": project_name,
            "project_level": "BASIC",
            "difficulty": 4,
            "description": "基于三端稳压管引脚缺陷检测案例的工业视觉项目交付全流程实训",
        },
    )
    project_id = project["id"]
    print(f"[1/9] 项目已创建：#{project_id} {project_name}")

    # 七个标准模块 + 每个模块的引导子标题（顺序即关卡顺序）
    for order, key in enumerate(STAGE_GUIDES, start=1):
        template = templates[key]
        await call(
            client,
            "post",
            f"/api/projects/{project_id}/modules",
            json={
                "template_id": template["id"],
                "stage_no": order,
                "weight": float(template["default_weight"]),
                "items_json": [{"title": title} for title in STAGE_GUIDES[key]],
            },
        )
    await call(client, "patch", f"/api/projects/{project_id}", json={"status": "PUBLISHED"})
    detail = await call(client, "get", f"/api/projects/{project_id}")
    print(f"[2/9] 七个模块已配置并发布，权重合计 {detail['weight_total']}")

    # 项目技能点：评审通过、项目完成时，技能进度就是按这里的关联重算的
    skill_nodes = await _ensure_skill_nodes(client)
    await call(
        client,
        "put",
        f"/api/projects/{project_id}/skills",
        json={"skill_node_ids": [node["id"] for node in skill_nodes]},
    )
    print(
        f"[3/9] 项目已关联 {len(skill_nodes)} 个技能点："
        + "、".join(node["node_name"] for node in skill_nodes)
    )

    # 项目附件：报告模板（给学生照格式写）+ 评分标准（AI 评审的依据）
    await call(
        client,
        "post",
        f"/api/projects/{project_id}/files/upload",
        files={"file": ("实训报告模板.md", REPORT_TEMPLATE_MD.encode(), "text/markdown")},
        data={"file_kind": "REPORT_TEMPLATE", "title": "实训报告模板"},
    )
    criteria = await call(
        client,
        "post",
        f"/api/projects/{project_id}/files/upload",
        files={"file": ("工业视觉检测项目评分标准.md", CRITERIA_MD.encode(), "text/markdown")},
        data={"file_kind": "SCORING_CRITERIA", "title": "工业视觉检测项目评分标准"},
    )
    doc_id = criteria["knowledge_doc_id"]
    print(f"[4/9] 报告模板已上传；评分标准入库：doc #{doc_id}，切片 {criteria['chunk_count']} 条")

    if run_review:
        embedded = await call(client, "post", f"/api/knowledge/docs/{doc_id}/embed")
        print(f"[5/9] 评分标准已向量化：{embedded['chunk_count']} 条 → {embedded['collection']}")
    else:
        print("[5/9] 跳过向量化（--no-review）")

    student = await call(
        client,
        "post",
        "/api/users",
        json={"user_no": user_no, "real_name": "案例演示学生", "user_type": "STUDENT"},
    )
    granted_role = await _ensure_student_role(client, user_id=student["id"])

    # 班级与岗位：审核列表的班级列、数据总览的岗位热度、学习过程统计、技能树都挂在
    # 这两条关联上；不建的话学生在业务上是"游离"的，下游页面全是空的。
    classroom = await _ensure_class(client, class_name=CLASS_NAME)
    await call(
        client,
        "post",
        f"/api/classes/{classroom['id']}/students",
        json={"user_no": user_no, "real_name": "案例演示学生"},
    )
    job = await _ensure_job(client, job_name=JOB_NAME)
    await call(
        client,
        "post",
        f"/api/students/{student['id']}/jobs",
        json={"job_id": job["id"], "is_primary": True},
    )
    job_skill_ids = {item["id"] for item in await call(client, "get", f"/api/jobs/{job['id']}/skills")}
    added_skills = 0
    for node in skill_nodes:
        if node["id"] not in job_skill_ids:
            await call(client, "post", f"/api/jobs/{job['id']}/skills/{node['id']}")
            added_skills += 1

    attempt = await call(client, "post", f"/api/students/{student['id']}/projects/{project_id}/start")
    extras = "、STUDENT 角色" if granted_role else ""
    print(
        f"[6/9] 学生 #{student['id']} 已入班「{classroom['class_name']}」、"
        f"选岗「{job['job_name']}」（补 {added_skills} 个岗位技能{extras}）；"
        f"开始闯关，轮次 #{attempt['id']}，共 {len(attempt['stages'])} 关"
    )

    # 逐关卡按引导子标题填写作答
    for stage in attempt["stages"]:
        key = stage["stage_key"]
        sections = ANSWERS.get(key)
        if not sections:
            continue
        await call(
            client,
            "patch",
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}",
            json={"answer_text": render_answer(sections)},
        )
    print(
        "[7/9] 七关作答已填写："
        + "、".join(f"{s['stage_name']}({len(ANSWERS.get(s['stage_key'], []))}项)" for s in attempt["stages"])
    )

    # 实训报告：以纯文本文件作为附件挂到"实训报告上传"关卡
    report_stage = next(s for s in attempt["stages"] if s["stage_key"] == "REPORT_UPLOAD")
    asset = await call(
        client,
        "post",
        "/api/file-assets/upload",
        files={"file": ("三端稳压管引脚缺陷检测实训报告.md", REPORT_MD.encode(), "text/markdown")},
        data={"biz_type": "REPORT", "uploader_id": student["id"]},
    )
    await call(
        client,
        "post",
        f"/api/attempts/{attempt['id']}/stages/{report_stage['id']}/files/{asset['id']}",
    )
    print(f"[8/9] 实训报告已上传并挂到「{report_stage['stage_name']}」：{asset['original_name']}")

    submission = await call(client, "post", f"/api/attempts/{attempt['id']}/submit")
    print(f"        整单提交：submission #{submission['id']}，状态 {submission['status']}")

    if not run_review:
        print("[9/9] 跳过 AI 评审（--no-review）")
        return

    print("[9/9] AI 评审中（RAG 召回评分标准 → DeepSeek 打分）……")
    started = time.perf_counter()
    result = await call(client, "post", f"/api/submissions/{submission['id']}/ai-review")
    elapsed = time.perf_counter() - started

    review = result["review"]
    print()
    print("=" * 72)
    print(f"评审结论：总分 {review['total_score']} / {review['conclusion']} / 状态 {review['status']}")
    print(f"打分模型：{result['model']}｜耗时 {elapsed:.1f}s")
    print(f"召回依据：{result['recalled_chunks']} 片，来自评分标准文档 #{result['criteria_doc_ids']}")
    print("-" * 72)
    for item in review["dimension_json"]:
        print(f"  {item['name']}：{item['score']} / {item['weight']} 分")
        print(f"    理由：{item['reason']}")
    print("-" * 72)
    print(f"总评：{review['comment']}")
    if result["warnings"]:
        print(f"提示：{result['warnings']}")
    print("=" * 72)

    provenance = review["raw_json"].get("attachments") or []
    print(f"附件台账：{[(a['name'], '已读' if a['readable'] else a['error']) for a in provenance]}")
    final = await call(client, "get", f"/api/submissions/{submission['id']}")
    print(f"结算后提交状态：{final['status']}，总分 {final['total_score']}，结论 {final['final_conclusion']}")
    print(f"评审记录：/api/submissions/{submission['id']}/reviews")

    # 技能进度是这条链路的"下游产物"：项目挂技能点 + 评审通过 → 结算时重算，
    # 这里打出来一眼就能看出关联有没有真的生效（全是 0 就说明 project_skill 没挂上）
    skills = await call(client, "get", f"/api/students/{student['id']}/skills")
    advanced = [item for item in skills if float(item.get("progress") or 0) > 0]
    print(
        f"技能进度：{len(advanced)}/{len(skills)} 个技能点已推进 —— "
        + "、".join(f"{item['node_name']} {float(item['progress']):.1f}%" for item in advanced)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按三端稳压管案例造一套实训数据并跑通 AI 评审")
    parser.add_argument(
        "--user-no", default="20260706", help="学生学号（重复执行时换一个，避免唯一约束冲突）"
    )
    parser.add_argument(
        "--project-name",
        default=PROJECT_NAME,
        help="项目名称（项目名唯一，重复执行时换一个；或先删掉同名项目）",
    )
    parser.add_argument("--no-review", action="store_true", help="只造数据，不调用大模型")
    return parser


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(run(user_no=args.user_no, project_name=args.project_name, run_review=not args.no_review))


if __name__ == "__main__":
    main()
