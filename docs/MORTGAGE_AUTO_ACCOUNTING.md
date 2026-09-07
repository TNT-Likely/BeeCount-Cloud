# 插件机制与房贷自动记账

BeeCount Cloud 通过通用插件运行时承载房贷等垂直记账能力，避免为每个业务场景在核心 write API 下新增专用 endpoint。

## 插件接口

```http
GET /api/v1/plugins
POST /api/v1/plugins/{plugin_id}/run
```

`GET /plugins` 返回插件清单和每个插件的 JSON Schema，Flutter / Web Console 可以据此动态渲染表单。

`POST /plugins/{plugin_id}/run` 负责执行插件，并把插件生成的结果写成普通 BeeCount 交易。写入仍走现有 write/sync/projection 流程，支持 `Idempotency-Key`，并会广播同步变更给其他客户端。

## 房贷插件

内置 reference plugin:

```text
mortgage_auto_accounting
```

执行示例：

```json
{
  "ledger_id": "ledger_xxx",
  "base_change_id": 0,
  "input": {
    "loan_name": "家庭房贷",
    "principal_amount": "1200000",
    "annual_rate_percent": "3.6",
    "term_months": 360,
    "start_date": "2026-06-20",
    "day_of_month": 20,
    "repayment_method": "equal_principal_interest",
    "account_name": "招商银行",
    "principal_category_name": "房贷本金",
    "interest_category_name": "房贷利息",
    "prepayment_category_name": "提前还款",
    "prepayments": [
      {
        "prepayment_date": "2027-01-10",
        "amount": "50000",
        "effect": "reduce_term"
      }
    ]
  }
}
```

## 还款方式

- `equal_principal_interest`: 等额本息。
- `equal_principal`: 等额本金。

金额计算在服务端使用 `Decimal` 和整数分完成，最后写入 BeeCount 交易时才转换成两位小数金额。

## 生成结果

每期还款会生成普通支出交易：

- 本金部分：默认分类 `房贷本金`。
- 利息部分：默认分类 `房贷利息`。
- 提前还款：默认分类 `提前还款`，并作为本金减少后续计划。
