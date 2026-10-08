# BeeCount Cloud 更新日志

本文件维护 Cloud 的版本摘要和升级影响。App 采用另一套版本号；Cloud 已发布记录见 [GitHub Releases](https://github.com/TNT-Likely/BeeCount-Cloud/releases)，官网中英摘要见 [Cloud 更新日志](https://count.beejz.com/docs/cloud-changelog)。历史补录精选 1.0.0–1.6.9 的核心发布和近期维护版，日期使用 GitHub Release 发布时间（UTC）。

## Unreleased

- MCP 小票附件上传与有序交易关联；本地 stdio 客户端可直接传文件路径，保留原始字节。
- Web 交易图片支持上传、移除、替换，并与 App 同步。
- 修复父分类改名后子分类关联、全量快照统计/预算标记，以及首页资产构成的多币种折算。

以上尚未对应新 Release。发版时按真实 tag 区间归入具体版本，补充发布时间、兼容性说明，并同步官网中文和英文页面。

## 1.6.9 · 2026-10-04

- **MCP 一致性修复**：改进交易关联与批量输入的一致性。
- **发布构建维护**：前端构建使用原生平台，避免 QEMU 构建问题。

[GitHub Release 1.6.9](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.9)

## 1.6.8 · 2026-10-04

- **项目合作与 AI 接入帮助**：Web 关于页提供合作入口，AI 配置页提供服务商接入指南；自建实例可配置关闭。
- **MCP 时间修复**：修正交易时区偏移。

[GitHub Release 1.6.8](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.8)

## 1.6.7 · 2026-09-25

- **账户余额校准**：支持直接校准余额，也可通过普通收支记录按差额平账；净值历史计算适配新的校准口径。

[GitHub Release 1.6.7](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.7)

## 1.6.6 · 2026-09-06

- **防止重复记账**：交易提交期间禁用重复点击，避免网页端重复创建交易。

[GitHub Release 1.6.6](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.6)

## 1.6.5 · 2026-09-04

- **混合文档检索**：AI 文档问答结合向量与关键词检索，提高检索覆盖。

[GitHub Release 1.6.5](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.5)

## 1.6.4 · 2026-09-03

- **文档索引动态更新**：支持更新文档索引并查看版本状态，无需为文档变化重建 Cloud 镜像。

[GitHub Release 1.6.4](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.4)

## 1.6.3 · 2026-08-13

- **智能记账多币种**：AI 识别可按对应币种记账。
- **周年皮肤同步**：Web 设置支持周年皮肤，带固定配色的皮肤同步更新主题色。

[GitHub Release 1.6.3](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.3)

## 1.6.2 · 2026-08-02

- **兼容性维护**：修复腾讯 COS 的 rclone provider 选择，并约束 MCP 依赖版本以维持服务兼容。

[GitHub Release 1.6.2](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.2)

## 1.6.1 · 2026-07-20

- **账户隐藏**：服务端同步账户隐藏状态，Web 支持隐藏账户；历史交易、余额与净资产继续保留。

[GitHub Release 1.6.1](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.1)

## 1.6.0 · 2026-07-12

- **交易级多币种**：交易保留原币金额，账本统计折算到主币种；编辑金额时联动更新折算结果。

[GitHub Release 1.6.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.6.0)

## 1.5.3 · 2026-07-04

- **MCP 传输升级**：改用 Streamable HTTP 单端点 `/api/v1/mcp`。这是需要调整客户端地址的兼容性变更；原 PAT 可继续使用。

**MCP 配置迁移**：旧 `/api/v1/mcp/sse` 和 `/api/v1/mcp/messages/` 不再提供。将客户端连接地址改为 `/api/v1/mcp`；[查看 MCP 配置](./docs/MCP.md)。

[GitHub Release 1.5.3](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.5.3)

## 1.5.2 · 2026-06-23

- **151 种货币**：扩展到完整 ISO 4217 货币列表并本地化显示名称。
- **备注显示方式**：Web 明细支持分类优先或备注优先。

[GitHub Release 1.5.2](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.5.2)

## 1.5.1 · 2026-06-19

- **账单标记**：支持“不计入收支”和“不计入预算”，服务端统计与 Web 一致处理；标记不改变账户余额。

[GitHub Release 1.5.1](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.5.1)

## 1.5.0 · 2026-06-12

- **主币种与汇率**：同步主币种、维护手动汇率，并支持汇率代理和 Web 折算视图。
- **净值趋势**：提供净值历史及资产走势/构成视图，资产汇总按主币种折算。

[GitHub Release 1.5.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.5.0)

## 1.4.0 · 2026-06-10

- **自定义每月起始日**：账本记账周期同步到统计、预算和 Web。
- **账户统计修复**：补全交易的账户关联，并回填历史数据，修复账户统计漏算。

[GitHub Release 1.4.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.4.0)

## 1.3.0 · 2026-05-20

- **共享账本**：支持 Owner 邀请 Editor 协同记账。
- **数据清理维护**：修复头像误判、数据库锁和删除卡住等问题。

[GitHub Release 1.3.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.3.0)

## 1.2.0 · 2026-05-13

- **MCP 与 PAT**：AI 客户端可直接查询、创建和编辑账本数据；Web 设置可查看 MCP 调用历史。
- **交易附件查看**：Web 交易详情支持附件缩略图与大图预览。

[GitHub Release 1.2.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.2.0)

## 1.1.0 · 2026-05-07

- **Web 工作台升级**：支持日历、年度报告、批量删除/导出，以及 CSV/TSV/Excel 导入、字段映射和预览。
- **AI 记账与文档问答**：支持截图/文字记账、AI 服务商配置与文档检索问答。
- **安全与备份**：新增 2FA/TOTP 及基于 rclone 的云端备份恢复。

[GitHub Release 1.1.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.1.0)

## 1.0.0 · 2026-04-19

- **自建云正式版**：Docker 镜像内置同步服务与 Web 控制台，支持 App/Web 双向实时同步、多用户独立数据和跨端偏好。
- **统一初始数据库结构**：正式版发布前整合初始迁移。

[GitHub Release 1.0.0](https://github.com/TNT-Likely/BeeCount-Cloud/releases/tag/1.0.0)
