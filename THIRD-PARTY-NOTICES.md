# 第三方组件声明 / Third-Party Notices

本仓库源码**不包含（vendor）任何第三方库的源代码**。所有第三方依赖在构建/部署时由包管理器（pip / pnpm）从官方源获取，各自按其原始开源协议授权，与本项目的 [LICENSE](LICENSE)（双许可）相互独立。使用、部署或基于本项目二次开发时，请一并遵守下列组件各自的协议。

> 下表为**直接依赖**及其上游申明的协议（截至 2026-07 整理，以各包发布页为准）。完整含传递依赖的清单可随时用 `pip-licenses`（后端）/ `pnpm licenses list`（前端）生成。

> **English**: This repository does **not vendor** any third-party source code. All dependencies are fetched at build/deploy time by pip / pnpm under their own licenses, independent of this project's dual-license model (see [LICENSE_EN](LICENSE_EN)). The tables below list **direct dependencies** with their upstream-declared licenses (as of 2026-07; upstream prevails). Note on **psycopg (LGPL-3.0)**: it is pip-installed, unmodified and used dynamically as a library, which does not affect the licensing of this project's own code (including closed-source commercial deployments); only redistributing psycopg itself triggers LGPL obligations.

## 后端（Python，见 requirements.txt）

| 组件 | 协议 |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| sqlalchemy | MIT |
| alembic | MIT |
| **psycopg** | **LGPL-3.0**（见下方说明） |
| pydantic / pydantic-settings | MIT |
| email-validator | CC0-1.0 |
| passlib | BSD |
| PyJWT | MIT |
| python-multipart | Apache-2.0 |
| apscheduler | MIT |
| pyzipper | MIT |
| pyotp | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| mcp | MIT |
| pytest（仅开发/测试） | MIT |

### 关于 psycopg（LGPL-3.0）

psycopg（PostgreSQL 驱动）采用 LGPL-3.0 协议。本项目**未修改** psycopg 源码、**未将其打包进本仓库**，仅在运行环境中经 pip 安装后以库的形式动态调用——这种使用方式下，LGPL 不影响本项目自身代码的授权方式（包括商业闭源部署）。若你需要**再分发 psycopg 本体**（例如打包进安装镜像并对外分发），请自行遵守 LGPL-3.0 的相应义务。

## 前端（pnpm workspace，见 frontend/）

| 组件 | 协议 |
|---|---|
| react / react-dom | MIT |
| react-router-dom | MIT |
| recharts | MIT |
| @radix-ui/*（accordion / dialog / dropdown-menu / select） | MIT |
| lucide-react | ISC |
| framer-motion | MIT |
| html-to-image | MIT |
| country-flag-icons | MIT |
| qrcode | MIT |
| cmdk | MIT |
| clsx / tailwind-merge | MIT |
| class-variance-authority | Apache-2.0 |
| tailwindcss / postcss / autoprefixer（构建期） | MIT |
| vite / vitest（构建期） | MIT |
| typescript（构建期） | Apache-2.0 |

---

## 静态图标资源（vendored assets）

下列图标以 SVG 文件形式随仓库分发（`frontend/apps/web/public/icons/`），不属于"构建期拉取的依赖"，故单独声明：

| 资源 | 来源 | 协议 |
|---|---|---|
| 国际卡组织 / 支付品牌图标（`icons/banks/intl/*.svg`） | [aaronfagan/svg-credit-card-payment-icons](https://github.com/aaronfagan/svg-credit-card-payment-icons)（logo 风格，经 jsDelivr 获取） | Apache-2.0 |
| 中国银行图标（`icons/banks/cn/*.svg`） | GitHub 开源银行 logo 集合 | 各品牌商标，仅作识别用途 |
| 账户 / 充值卡图标（`icons/account/*.svg`） | 项目自绘（Lucide 风格） | 随本项目许可 |

各品牌 logo 商标权归其所有者；本项目仅用于账户类型识别，不主张任何权利。详见 `icons/banks/intl/NOTICE.md`。

---

如发现本清单与上游实际协议不符，以上游为准，并欢迎提 Issue 指正。
