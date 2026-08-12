# 底层软件 SDD 技能包

这是一套可独立调用、也可由统一流程编排的初版 Specification-Driven Development（SDD）技能。它面向驱动、固件、设备管理、协议栈、硬件适配层和系统工具等底层软件；当前 AutoEnv 只是形成这套方法的实践样本，不是模板中的默认架构。

## 从哪里开始

- 完整生命周期：`sdd-process`
- 解析存量代码并建立 design/spec：`analyze-existing-system`
- 初始化或修订仓库 `AGENTS.md`：`init-agents-md`
- 增量需求设计、实现和文档回写：`develop-incremental-requirement`
- 架构评审：`review-architecture`
- 独立门禁判定：`run-sdd-checkpoint`
- 交付前自评审与建议判决：`self-review-and-verdict`
- 从对话和证据形成流程改进提案：`conduct-sdd-retrospective`

每个 skill 都必须先读取 [`common/PROCESS_CONTRACT.md`](common/PROCESS_CONTRACT.md)。模板位于 [`common/templates/`](common/templates/)，覆盖 manifest、顶层/模块设计、feature spec、增量计划、ADR、验证计划、评审、AGENTS 和复盘提案。机器校验入口是：

```powershell
python -X utf8 sdd/common/scripts/validate_sdd.py --skills-root sdd
```

## 核心可信约束

1. 事实、用户先验、推断和未知项必须分开标记；无代码位置、命令输出或测试报告的结果不得写成“已验证”。
2. 需求必须可追踪到设计、代码、测试和证据；关键需求覆盖率不是 100% 时不得 PASS。
3. Agent 只能给出 `PASS/CONDITIONAL_PASS/FAIL/BLOCKED` 建议判决，不能替代人批准基线、风险豁免或复盘写入。
4. 目标机测试、硬件在环测试和故障注入未执行时，必须明确写成未验证；主机 UT 不能替代目标机证据。
5. 复盘先生成提案。用户确认前，不得修改 skill、模板或流程基线。

## 推荐产物布局

```text
docs/sdd/
├── manifest.md
├── design.md
├── specs/
│   └── <feature>/spec.md
├── modules/
│   └── <module>/design.md
├── changes/
│   └── <change-id>/change-plan.md
├── reviews/
│   ├── architecture-review.md
│   ├── checkpoint-report.md
│   └── self-review.md
└── retrospectives/
    └── <date>-proposal.md
```

## 状态与职责

文档状态只使用 `draft`、`in_review`、`approved`、`superseded`。Agent 可创建前两种状态；只有明确的人类确认才能标为 `approved`。批准后的内容需要通过增量变更记录修订，不允许静默覆盖。
