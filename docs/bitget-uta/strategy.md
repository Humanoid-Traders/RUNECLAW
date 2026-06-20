# Bitget UTA API — Strategy / Plan orders (TP/SL, triggers)

| Endpoint / Channel | Slug |
| --- | --- |
| [Place Strategy Order](#place-strategy-order) | `strategy/Place-Strategy-Order` |
| [Modify Strategy Order](#modify-strategy-order) | `strategy/Modify-Strategy-Order` |
| [Cancel Strategy Order](#cancel-strategy-order) | `strategy/Cancel-Strategy-Order` |
| [Get Unfilled Strategy Orders](#get-unfilled-strategy-orders) | `strategy/Get-Unfilled-Strategy-Orders` |
| [Get History Strategy Orders](#get-history-strategy-orders) | `strategy/Get-History-Strategy-Orders` |


---

# Place Strategy Order

### Description

Place a strategy order

- **API Broker rebate identifier**: The following code block needs to be added to the HTTP Header of the request."X-CHANNEL-API-CODE":"your-channel-api-code"

### HTTP Request

- POST /api/v3/trade/place-strategy-order
- Speed limit is 10 times/s (UID)
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/place-strategy-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{ "category": "usdt-futures","symbol": "BTCUSDT","posSide": "long","stopLoss": "99000","takeProfit": "100800","clientOid": "121211212122"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| clientOid | String | No | Client order ID<br> The idempotent validity period is six hours (not fully guaranteed) |
| type | String | No | Strategy Type<br>`tpsl` Take-Profit and Stop-Loss<br>`trigger` Trigger Order<br>Default:`tpsl` |
| tpslMode | String | No | Take-Profit and Stop-Loss Mode<br>`full`All Positions Take-Profit and Stop-Loss<br>`partial`Partial Position Take-Profit and Stop-Loss<br>If left blank, the default value is `full` |
| qty | String | No | Order Quantity<br>Required when `tpslMode=partial` for take-profit/stop-loss orders; required for trigger orders. Unit is in the `base coin` |
| side | String | No | Trade side<br>`buy`/`sell`<br>Hedge-mode:<br>Open long: `side=buy` & `posSide=long`<br>Open short: `side=sell` & `posSide=short`<br>Close long: `side=sell` & `posSide=long`<br>Close short: `side=buy` & `posSide=short`<br>One-way mode:<br>Open long: `side=buy`<br>Open short: `side=sell`<br>Close long: `side=sell` & `reduceOnly=yes`<br>Close short: `side=buy` & `reduceOnly=yes` |
| posSide | String | No | Position side<br>`long`/`short` |
| reduceOnly | String | No | Whether it is reduce-only<br>`yes`/`no` |
| tpTriggerBy | String | No | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br>If not specified, the default value is market price |
| slTriggerBy | String | No | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br>If not filled in, the default value is market price |
| takeProfit | String | No | Take-Profit Trigger Price |
| stopLoss | String | No | Stop-Loss Trigger Price |
| tpOrderType | String | No | Take-Profit Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br>If not filled in, the default value is market price |
| slOrderType | String | No | Stop-Loss Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br>If not filled in, the default value is market price |
| tpLimitPrice | String | No | Take-Profit Strategy Order Execution Price<br>This field is only valid for limit orders (when `tpOrderType=limit`); it is ignored for market orders. |
| slLimitPrice | String | No | Stop-Loss Strategy Order Execution Price<br>This field is only valid for limit orders (when `slOrderType=limit`); it is ignored for market orders |
| triggerBy | String | No | Trigger order trigger price type<br>`market`: Market Price<br>`mark`: Mark Price<br>If not specified, the default value is market price |
| triggerPrice | String | No | Trigger order trigger price<br>Only valid for limit orders (when `triggerOrderType=limit`); ignored for market orders |
| triggerOrderType | String | No | Trigger order type<br>`limit`: Limit Order<br>`market`: Market Order |
| triggerOrderPrice | String | No | Trigger order execution price<br>Only valid for limit orders (when `triggerOrderType=limit`); ignored for market orders |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": {
    "clientOid": "121211212122",
    "orderId": "121211212122"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |

[Source](https://www.bitget.com/api-doc/uta/strategy/Place-Strategy-Order)


---

# Modify Strategy Order

### Description

Modify strategy order

### HTTP Request

- POST /api/v3/trade/modify-strategy-order
- Speed limit is 10 times/s (UID)
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/modify-strategy-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"orderId": "121211212122","qty": "1","tpTriggerBy": "market","takeProfit": "106000","tpOrderType": "market"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| orderId | String | Yes | Order ID<br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| clientOid | String | No | Client order ID <br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| qty | String | Yes | Order Quantity<br>Can be modified under partial take-profit/stop-loss mode, and the unit is in the `base coin` |
| tpTriggerBy | String | No | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| slTriggerBy | String | No | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| takeProfit | String | No | Take-Profit Trigger Price |
| stopLoss | String | No | Stop-Loss Trigger Price |
| tpOrderType | String | No | Take-Profit Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| slOrderType | String | No | Stop-Loss Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| tpLimitPrice | String | No | Take-Profit Strategy Order Execution Price<br>This field is only valid for limit orders (when `tpOrderType=limit`); it is ignored for market orders. |
| slLimitPrice | String | No | Stop-Loss Strategy Order Execution Price<br>This field is only valid for limit orders (when `slOrderType=limit`); it is ignored for market orders |
| triggerBy | String | No | Trigger order trigger price type<br>`market`: Market Price<br>`mark`: Mark Price |
| triggerPrice | String | No | Trigger order trigger price<br>Only valid for limit orders (when `triggerOrderType=limit`) |
| triggerOrderType | String | No | Trigger order type<br>`limit`: Limit Order<br>`market`: Market Order |
| triggerOrderPrice | String | No | Trigger order execution price<br>Only valid for limit orders (when `triggerOrderType=limit`) |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": {
    "clientOid": "121211212122",
    "orderId": "121211212122"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |

[Source](https://www.bitget.com/api-doc/uta/strategy/Modify-Strategy-Order)


---

# Cancel Strategy Order

### Description

Cancel strategy order

### HTTP Request

- POST /api/v3/trade/cancel-strategy-order
- Speed limit is 10 times/s (UID)
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/cancel-strategy-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"orderId":"111111111111111111"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| orderId | String | Yes | Order ID<br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| clientOid | String | No | Client order ID <br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": null
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| code | String | Code |
| msg | String | Msg |
| requestTime | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/strategy/Cancel-Strategy-Order)


---

# Unfilled Strategy Orders

### Description

Get unfilled strategy orders

### HTTP Request

- GET /api/v3/trade/unfilled-strategy-orders
- Speed limit is 20 times/s (UID)
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/unfilled-strategy-orders?category=usdt-futures&type=tpsl" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" 
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| type | String | No | Strategy Type<br>`tpsl` Take-Profit and Stop-Loss<br>`trigger` Trigger Order |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730186730084,
  "data": [
    {
      "orderId": "111111111111111111",
      "clientOid": "111111111111111111",
      "category": "USDT-FUTURES",
      "symbol": "BTCUSDT",
      "qty": "0.01",
      "posSide": "long",
      "status": "pending",
      "tpTriggerBy": "market",
      "slTriggerBy": "market",
      "takeProfit": "110000",
      "stopLoss": "90000",
      "tpOrderType": "market",
      "slOrderType": "market",
      "tpLimitPrice": "91000",
      "slLimitPrice": "111000",
      "triggerBy": "market",
      "triggerPrice": "100000",
      "triggerOrderType": "limit",
      "triggerOrderPrice": "100500",
      "createdTime": "1730186725663",
      "updatedTime": "1730186725691"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| data | Array | Order list |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >category | String | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name <br>e.g.,`BTCUSDT` |
| >qty | String | Order Quantity |
| >posSide | String | Position side<br>`long`/`short` |
| >status | String | Strategy order status<br>`pending` Waiting to be executed<br>`success` Executed<br>`failed` Execution failed<br>`cancelled` Cancelled<br>`submitting` Submitting |
| >tpTriggerBy | String | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| >slTriggerBy | String | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| >takeProfit | String | Take-Profit Trigger Price |
| >stopLoss | String | Stop-Loss Trigger Price |
| >tpOrderType | String | Take-Profit Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| >slOrderType | String | Stop-Loss Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| >tpLimitPrice | String | Take-Profit Strategy Order Execution Price |
| >slLimitPrice | String | Stop-Loss Strategy Order Execution Price |
| >triggerBy | String | Trigger order trigger price type<br>`market`: Market Price<br>`mark`: Mark Price |
| >triggerPrice | String | Trigger order trigger price |
| >triggerOrderType | String | Trigger order type<br>`limit`: Limit Order<br>`market`: Market Order |
| >triggerOrderPrice | String | Trigger order execution price |
| >createdTime | String | Order created timestamp<br>A Unix millisecond timestamp |
| >updatedTime | String | Order update timestamp<br>A Unix millisecond timestamp |

[Source](https://www.bitget.com/api-doc/uta/strategy/Get-Unfilled-Strategy-Orders)


---

# History Strategy Orders

### Description

Get historical strategy orders

### HTTP Request

- GET /api/v3/trade/history-strategy-orders
- Speed limit is 20 times/s (UID)
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/history-strategy-orders?category=usdt-futures&type=tpsl" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" 
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| type | String | No | Strategy Type<br>`tpsl` Take-Profit and Stop-Loss<br>`trigger` Trigger Order |
| startTime | String | No | Start timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085` |
| endTime | String | No | End timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085` |
| limit | String | No | Limit per page<br>Default:`100`. Maximum:`100` |
| cursor | String | No | Cursor <br>Pagination is implemented by omitting the cursor in the first query and applying the cursor from the previous query for subsequent pages |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730186730084,
  "data": {
    "list": [
      {
        "orderId": "111111111111111111",
        "clientOid": "111111111111111111",
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "qty": "0.01",
        "posSide": "long",
        "status": "success",
        "tpTriggerBy": "market",
        "slTriggerBy": "market",
        "takeProfit": "110000",
        "stopLoss": "90000",
        "tpOrderType": "market",
        "slOrderType": "market",
        "tpLimitPrice": "91000",
        "slLimitPrice": "111000",
        "triggerBy": "market",
        "triggerPrice": "100000",
        "triggerOrderType": "limit",
        "triggerOrderPrice": "100500",
        "createdTime": "1730186725663",
        "updatedTime": "1730186725691"
      }
    ],
    "cursor": "1233319323918499840"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | Order list |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >category | String | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name <br>e.g.,`BTCUSDT` |
| >qty | String | Order Quantity |
| >posSide | String | Position side<br>`long`/`short` |
| >status | String | Strategy order status<br>`pending` Waiting to be executed<br>`success` Executed<br>`failed` Execution failed<br>`cancelled` Cancelled<br>`submitting` Submitting |
| >triggerType | String | Trigger Type<br>`takeProfit`Take-Profit<br>`stopLoss`Stop-Loss |
| >tpTriggerBy | String | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| >slTriggerBy | String | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br> |
| >takeProfit | String | Take-Profit Trigger Price |
| >stopLoss | String | Stop-Loss Trigger Price |
| >tpOrderType | String | Take-Profit Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| >slOrderType | String | Stop-Loss Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order<br> |
| >tpLimitPrice | String | Take-Profit Strategy Order Execution Price |
| >slLimitPrice | String | Stop-Loss Strategy Order Execution Price |
| >triggerBy | String | Trigger order trigger price type<br>`market`: Market Price<br>`mark`: Mark Price |
| >triggerPrice | String | Trigger order trigger price |
| >triggerOrderType | String | Trigger order type<br>`limit`: Limit Order<br>`market`: Market Order |
| >triggerOrderPrice | String | Trigger order execution price |
| >createdTime | String | Order created timestamp<br>A Unix millisecond timestamp |
| >updatedTime | String | Order update timestamp<br>A Unix millisecond timestamp |
| cursor | String | Cursor for next page<br>Pass this value as the `cursor` parameter in the next request to get the next page of data |

[Source](https://www.bitget.com/api-doc/uta/strategy/Get-History-Strategy-Orders)

