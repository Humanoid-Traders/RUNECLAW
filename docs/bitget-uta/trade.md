# Bitget UTA API — Trade (orders & positions)

| Endpoint / Channel | Slug |
| --- | --- |
| [Place Order](#place-order) | `trade/Place-Order` |
| [Place Batch](#place-batch) | `trade/Place-Batch` |
| [Modify Order](#modify-order) | `trade/Modify-Order` |
| [Batch Modify Orders](#batch-modify-orders) | `trade/Batch-Modify-Orders` |
| [Cancel Order](#cancel-order) | `trade/Cancel-Order` |
| [Cancel Batch](#cancel-batch) | `trade/Cancel-Batch` |
| [Cancel All Order](#cancel-all-order) | `trade/Cancel-All-Order` |
| [Close All Positions](#close-all-positions) | `trade/Close-All-Positions` |
| [CountDown Cancel All](#countdown-cancel-all) | `trade/CountDown-Cancel-All` |
| [Get Order Details](#get-order-details) | `trade/Get-Order-Details` |
| [Get Order Pending](#get-order-pending) | `trade/Get-Order-Pending` |
| [Get Order History](#get-order-history) | `trade/Get-Order-History` |
| [Get Order Fills](#get-order-fills) | `trade/Get-Order-Fills` |
| [Get Position](#get-position) | `trade/Get-Position` |
| [Get Position History](#get-position-history) | `trade/Get-Position-History` |
| [Get Position ADL Rank](#get-position-adl-rank) | `trade/Get-Position-ADL-Rank` |
| [Get Max Open Available](#get-max-open-available) | `trade/Get-Max-Open-Available` |


---

# Place Order

### Description

This endpoint allows the order placement across spot, margin, or futures markets with customizable parameters, including price, quantity, and order type, etc.

- **Futures** For one-way mode, reduce-only orders are allowed to place. If a reduce only order already exists and the order quantity equals the position size, or if a new reduce only order exceeds the remaining position size, the previous reduction order will be automatically canceled and replaced. In this case, the returned orderId will be null. **It is recommended to always provide a `clientOid`.**

- **Margin** Margin orders will automatically trigger fund borrowing.

- **Order Check**
  - **Futures**:`price` must meet the price multiplier and be a multiple of `priceMultiplier`, and conform to the `pricePrecision` decimal places. `qty` must be greater than or equal to `minOrderAmount` and be a multiple of `sizeMultiplier`.
  - **Spot**:`price` must meet the decimal place requirement. `qty` must be greater than or equal to `minOrderAmount`.

- **Open Position Logic**
  - **Hedge-mode** Open long: `side=buy` & `posSide=long` Open Short: `side=sell` & `posSide=short` Close long: `side=sell` & `posSide=long` Close short: `side=buy` & `posSide=short`
  - **One-way-mode** Open long: `side=buy` Open short: `side=sell` Close long: `side=sell` & `reduceOnly=yes` Close short: `side=buy` & `reduceOnly=yes`

- **Order Limit**
  - **Futures**: 400 orders across all USDT, Coin-M, and USDC futures trading pairs.
  - **Spot**: 400 orders across all spot and margin trading pairs.

- **ClientOid Constraints** Please ensure your clientOid matches the regular expression `^[\.A-Z\:/a-z0-9_-]{1,32}$`, consisting of 1 to 32 characters, including periods (.), uppercase letters, colons (:), lowercase letters, numbers, underscores (_), and hyphens (-).

- **Request Monitor** The API requests will be monitored. If the total number of orders for a single account (including master and sub-accounts) exceeds a set daily limit (UTC 00:00 - UTC 24:00), the platform reserves the right to issue reminders, warnings, and enforce necessary restrictions. By using the API, clients acknowledge and agree to comply with these terms.

- **API Broker rebate identifier**: The following code block needs to be added to the HTTP Header of the request."X-CHANNEL-API-CODE":"your-channel-api-code"
- **Error Sample** { "code":"40762", "msg":"The order size is greater than the max open size", "requestTime":1627293504612 } This error code may occur in the following scenarios.Insufficient account balance. The position tier for this symbol has reached its limit. For details on specific tiers, please refer here .
- **COIN-M Futures Symbol Format Description:**
  - **The symbol format for the new COIN-M business line is "XXXUSD_CM". For example, the BTCUSD trading pair in COIN-M futures is formatted as BTCUSD_CM.**
  - The new COIN-M business line does not support modifying orders, ADL, strategy orders, or preset take-profit/stop-loss orders.

Note: If the following errors occur when placing an order, please use `clientOid` to query the order details to confirm the final result of the operation.

> { "code": "40010", "msg": "Request timed out", "requestTime": 1666268894074, "data": null }
>  { "code": "40725", "msg": "service return an error", "requestTime": 1666268894071, "data": null }
>  { "code": "45001", "msg": "Unknown error", "requestTime": 1666268894071, "data": null }

### HTTP Request

- POST /api/v3/trade/place-order
- Rate limit: 10/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/place-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"category":"SPOT","symbol":"BGBUSDT","orderType":"limit","qty":"123","price":"1.11","side":"buy","posSide":"long","timeInForce":"gtc","reduceOnly":"no"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| qty | String | Yes | Order quantity <br>`Spot/Margin`<br> For market buy orders,the unit is **quote coin**<br>For limit and market sell orders, the unit is **base coin**<br> `USDT/USDC-Futures` <br> The unit is **base coin** <br> `COIN-Futures` <br> The unit is **quote coin** |
| price | String | No | Order price <br> This field is required when the order type is a `limit` order .<br>This field is not applicable when the order type is a `market` order. |
| side | String | Yes | Order side<br>`buy`/`sell` |
| orderType | String | Yes | Order type<br>`limit`/`market` |
| timeInForce | String | No | Time in force<br>`ioc` Immediate or cancel. It must be executed immediately, with any unfilled portion canceled. <br>`fok` Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br>`gtc` Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`post_only` Post only. It will only be added to the order book as a maker.<br>`rpi` Retail Price Improvement order. A non-displayed limit order that provides price improvement for retail order flow. Only available for accounts with RPI market maker permissions.<br>This field is required when orderType is `limit`. If omitted, it defaults to `gtc` |
| posSide | String | No | Position side<br>`long`/`short`<br>This field is required in hedge-mode position.<br> Available only for futures |
| clientOid | String | No | Client order ID |
| reduceOnly | String | No | Reduce-only identifier<br>`yes`/`no`, default `no`; <br>`yes` indicates that your position may only be reduced in size upon the activation of this order |
| stpMode | String | No | STP Mode(Self Trade Prevention)<br> `none`: not setting STP(default)<br>`cancel_taker`: cancel taker order <br>`cancel_maker`: cancel maker order <br>`cancel_both`: cancel both of taker and maker orders |
| tpTriggerBy | String | No | Preset Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br>If not specified, the default value is market price<br>Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures. |
| slTriggerBy | String | No | Preset Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price<br>If not filled in, the default value is market price<br>Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures |
| takeProfit | String | No | Preset Take-Profit Trigger Price |
| stopLoss | String | No | Preset Stop-Loss Trigger Price |
| tpOrderType | String | No | Take-Profit Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| slOrderType | String | No | Stop-Loss Trigger Strategy Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| tpLimitPrice | String | No | Take-Profit Strategy Order Execution Price<br>This field is only valid for limit orders (when `tpOrderType=limit`); it is ignored for market orders. |
| slLimitPrice | String | No | Stop-Loss Strategy Order Execution Price<br>This field is only valid for limit orders (when `slOrderType=limit`); it is ignored for market orders. |
| marginMode | String | No | Margin mode<br>`crossed` Cross margin<br>`isolated` Isolated margin<br>If not provided, defaults to cross margin<br>Available only for futures |

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

[Source](https://www.bitget.com/api-doc/uta/trade/Place-Order)


---

# Batch Order

### Description

This endpoint allows the order placement across spot, margin, or futures markets with customizable parameters, including price, quantity, and order type, etc.

- **Upcoming Features** (currently not supported)
  - Isolated spot margin will be available soon.
  - Copy trading order placement will be available soon.
  - Take profit and stop loss will be available soon.

- **Futures** For one-way mode, reduce-only orders are allowed to place. If a reduce only order already exists and the order quantity equals the position size, or if a new reduce only order exceeds the remaining position size, the previous reduction order will be automatically canceled and replaced. In this case, the returned orderId will be null. * *It is recommended to always provide a `clientOid`.（currently,`reduce-only` orders are not supported）**

- **Margin** Margin orders will automatically trigger fund borrowing.

- **Order Check**
  - **Futures**:`price` must meet the price multiplier and be a multiple of `priceMultiplier`, and conform to the `pricePrecision` decimal places. `qty` must be greater than or equal to `minOrderAmount` and be a multiple of `sizeMultiplier`.
  - **Spot**:`price` must meet the decimal place requirement. `qty` must be greater than or equal to `minOrderAmount`.

- **Open Position Logic**
  - **Hedge-mode** Open long: `side=buy` & `posSide=long` Open Short: `side=sell` & `posSide=short` Close long: `side=sell` & `posSide=long` Close short: `side=buy` & `posSide=short`
  - **One-way-mode** Open long: `side=buy` Open short: `side=sell` Close long: `side=sell` & `reduceOnly=yes` Close short: `side=buy` & `reduceOnly=yes`

- **Order Limit**
  - **Futures**: 400 orders across all USDT, Coin-M, and USDC futures trading pairs.
  - **Spot**: 400 orders across all spot and margin trading pairs.

- **ClientOid Constraints** Please ensure your clientOid matches the regular expression `^[\.A-Z\:/a-z0-9_-]{1,32}$`, consisting of 1 to 32 characters, including periods (.), uppercase letters, colons (:), lowercase letters, numbers, underscores (_), and hyphens (-).

- **Request Monitor** The API requests will be monitored. If the total number of orders for a single account (including master and sub-accounts) exceeds a set daily limit (UTC 00:00 - UTC 24:00), the platform reserves the right to issue reminders, warnings, and enforce necessary restrictions. By using the API, clients acknowledge and agree to comply with these terms.

- **API Broker rebate identifier**: The following code block needs to be added to the HTTP Header of the request."X-CHANNEL-API-CODE":"your-channel-api-code"
- **Error Sample** { "code":"40762", "msg":"The order size is greater than the max open size", "requestTime":1627293504612 } This error code may occur in the following scenarios.Insufficient account balance. The position tier for this symbol has reached its limit. For details on specific tiers, please refer here .

Note: If the following errors occur when placing an order, please use `clientOid` to query the order details to confirm the final result of the operation.

> { "code": "40010", "msg": "Request timed out", "requestTime": 1666268894074, "data": null }
>  { "code": "40725", "msg": "service return an error", "requestTime": 1666268894071, "data": null }
>  { "code": "45001", "msg": "Unknown error", "requestTime": 1666268894071, "data": null }

### HTTP Request

- POST /api/v3/trade/place-batch
- Rate limit: 5/sec/UID
- Batch limit: No more than 20 orders per batch
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/place-batch" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '[{"category":"SPOT","symbol":"BGBUSDT","orderType":"limit","qty":"123","price":"1.11","side":"buy","timeInForce":"gtc","clientOid":"my-oid-1"}]'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures <br>All orders must have the same category |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| qty | String | Yes | Order quantity <br>`Spot/Margin`<br> For market buy orders,the unit is **quote coin**<br>For limit and market sell orders, the unit is **base coin**<br> `USDT/USDC-Futures` <br> The unit is **base coin** <br> `COIN-Futures` <br> The unit is **quote coin** |
| price | String | No | Order price <br>This field is required when orderType is `limit` |
| side | String | Yes | Order side<br>`buy`/`sell` |
| orderType | String | Yes | Order type<br>`limit`/`market` |
| timeInForce | String | No | Time in force<br>`ioc` Immediate or cancel. It must be executed immediately, with any unfilled portion canceled. <br>`fok` Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br>`gtc` Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`post_only` Post only. It will only be added to the order book as a maker.<br>`rpi` Retail Price Improvement order. A non-displayed limit order that provides price improvement for retail order flow. Only available for accounts with RPI market maker permissions.<br>This field is required when orderType is `limit`. If omitted, it defaults to `gtc` |
| posSide | String | No | Position side<br>`long`/`short`<br>This field is required in hedge-mode position.<br> Available only for futures |
| clientOid | String | No | Client order ID |
| stpMode | String | No | STP Mode, default `none` <br> `none` not setting STP <br> `cancel_taker` cancel taker order <br> `cancel_maker` cancel maker order <br> `cancel_both` cancel both of taker and maker orders |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": [
    {
      "clientOid": "121211212122",
      "orderId": "121211212122"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |
| code | String | Error code for this order |
| msg | String | Error message for this order |

[Source](https://www.bitget.com/api-doc/uta/trade/Place-Batch)


---

# Modify Order

### Description

Support modifying orders using either the order ID (orderId) or a custom order ID (clientOid).

- Only orders that have not been fully filled can be modified. If an order has been completely filled, it cannot be modified through this interface.
- After submitting a modification request and before receiving the result, repeated modification requests cannot be submitted.

### HTTP Request

- POST /api/v3/trade/modify-order
- Rate limit: 10/sec/UID
- Permission: UTA trade
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/modify-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{ "orderId": "1","qty": "123", "price": "123", "autoCancel": "no" }'
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| orderId | String | No | Order ID<br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| clientOid | String | No | Client order ID <br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| qty | String | No | Order quantity <br>`Base coin` <br>Either qty or price must be provided |
| price | String | No | Order price <br>Either qty or price must be provided |
| autoCancel | String | No | Will the original order be canceled if the order modification fails<br>`yes`: Cancel <br>`no`: Not cancel（default）<br>When set to `yes`: if the matching engine fails to modify the order, the order is cancelled immediately; after cancellation, the counter will reject any further modification requests for that order (including in-flight and new requests). |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |
| category | String | No | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |

Response

```
{
  "code": "00000",
  "data": {
    "orderId": "121212121212",
    "clientOid": "BITGET#1627293504612"
  },
  "msg": "success",
  "requestTime": 1627293504612
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |

[Source](https://www.bitget.com/api-doc/uta/trade/Modify-Order)


---

# Batch Modify Orders

### Description

- Supports batch order modification via API, allowing simultaneous submission of multiple orders across different trading pairs (limited to orders within the same business line).
- Each request supports modification of up to 20 orders.
- Supports continuous order modification, meaning additional modification requests can be submitted before the previous modification request is completed. A maximum of 5 consecutive modification requests for in-progress orders can be submitted, and the matching engine will process the modification requests in sequence.
- Within the same batch of modification requests, each order can only appear once.
- Only fully unfilled orders can have their price and quantity modified.
- Partially filled orders can have their price and quantity modified (the modified quantity cannot be less than the already filled quantity).
- Modification of reduce-only orders is not supported.

### HTTP Request

- POST /api/v3/trade/batch-modify-order
- Rate limit: 10/sec/UID
- Permission: UTA trade
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/batch-modify-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '[{ "orderId": "1","qty": "123", "price": "123", "autoCancel": "no" },{ "orderId": "2","qty": "123", "price": "123", "autoCancel": "no" }]'
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| orderId | String | No | Order ID<br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| clientOid | String | No | Client order ID <br>Either orderId or clientOid must be provided<br>If both orderId and clientOid are provided simultaneously, orderId takes higher priority |
| qty | String | No | Order quantity <br>`Base coin` |
| price | String | No | Order price |
| autoCancel | String | No | Will the original order be canceled if the order modification fails<br>`yes`: Cancel <br>`no`: Not cancel（default）<br>When set to `yes`: if the matching engine fails to modify the order, the order is cancelled immediately; after cancellation, the counter will reject any further modification requests for that order (including in-flight and new requests). |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |
| category | String | No | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |

Response

```
{
  "code": "00000",
  "data": [
    {
      "orderId": "1",
      "clientOid": "12312"
    },
    {
      "orderId": "2",
      "clientOid": "2321"
    }
  ],
  "msg": "success",
  "requestTime": 1627293504612
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |

[Source](https://www.bitget.com/api-doc/uta/trade/Batch-Modify-Orders)


---

# Cancel Order

### Description

This endpoint allows you to cancel a single unfilled or partially filled order across spot, margin, and futures markets.

### HTTP Request

- POST /api/v3/trade/cancel-order
- Rate limit: 10/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/cancel-order" \
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
| orderId | String | No | Order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| clientOid | String | No | Client order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| category | String | No | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": {
    "clientOid": "121211212122",
    "orderId": "111111111111111111"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |

[Source](https://www.bitget.com/api-doc/uta/trade/Cancel-Order)


---

# Batch Cancel

### Description

- This endpoint allows you to cancel a multiple unfilled or partially filled order across spot, margin, and futures markets.
- When making a batch order cancellation, ensure that each request uses either orderId or clientOid for identification — never both. If both orderId and clientOid are provided in a single request, the clientOid will be ignored.
- Batch order cancellation allows partial success.

### HTTP Request

- POST /api/v3/trade/cancel-batch
- Rate limit: 5/sec/UID
- Batch limit: No more than 20 orders per batch
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/cancel-batch" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '[{"orderId":"112233","category":"SPOT","symbol":"BTCUSDT"},{"clientOid":"123456","category":"SPOT","symbol":"BTCUSDT"}]'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| orderId | String | No | Order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| clientOid | String | No | Client order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| category | String | YES | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures <br>All orders must have the same category |
| symbol | String | YES | Symbol name <br>e.g.,`BTCUSDT` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695806875837,
  "data": [
    {
      "clientOid": "121211212122",
      "orderId": "111111111111111111"
    },
    {
      "clientOid": "121211212123",
      "orderId": "111111111111111114"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |
| code | String | Error code for this order |
| msg | String | Error message for this order |

[Source](https://www.bitget.com/api-doc/uta/trade/Cancel-Batch)


---

# Cancel All Orders

### Description

Cancel unfilled or partially filled orders by symbol or category.

### HTTP Request

- POST /api/v3/trade/cancel-symbol-order
- Rate limit: 5/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/cancel-symbol-order" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"category":"SPOT"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT`<br>If no symbol is provided, all pending orders in the corresponding category will be closed. |

Response

```
{
  "code": "00000",
  "data": {
    "list": [
      {
        "orderId": "111111111111111111",
        "clientOid": "111111111111111111",
        "code": "24056",
        "msg": "notExisted"
      }
    ]
  },
  "msg": "success",
  "requestTime": 1627293504612
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | List |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >msg | String | Message |
| >code | String | Code |

[Source](https://www.bitget.com/api-doc/uta/trade/Cancel-All-Order)


---

# Close All Positions

### Description

Close positions by position side or category. All positions will be closed at market price, subject to slippage.

- **API Broker rebate identifier**: The following code block needs to be added to the HTTP Header of the request."X-CHANNEL-API-CODE":"your-channel-api-code"

### HTTP Request

- POST /api/v3/trade/close-positions
- Rate limit: 5/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/close-positions" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"category":"USDT-FUTURES"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT`<br>If no symbol is provided, all positions in the corresponding category will be closed. |
| posSide | String | No | Position side <br>`long`/`short` <br> If this field is provided, only the position in the corresponding side will be closed. |

Response

```
{
  "code": "00000",
  "data": {
    "list": [
      {
        "orderId": "111111111111111111",
        "clientOid": "111111111111111111",
        "code": "24056",
        "msg": "notExisted"
      }
    ]
  },
  "msg": "success",
  "requestTime": 1627293504612
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | List |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >msg | String | Message |
| >code | String | Code |

[Source](https://www.bitget.com/api-doc/uta/trade/Close-All-Positions)


---

# CountDown Cancel All

### Description

In practical use, clients need to periodically send heartbeat requests to prevent uncontrolled open orders due to abnormal disconnections or system crashes.
 For example, if your expected heartbeat interval is 10 seconds, meaning if no heartbeat request is sent for over 10 seconds, all orders must be canceled:

- Under normal circumstances, you can call this interface every 3-5 seconds, set the countdown to 10, and repeatedly call it to reset the countdown.
- Under abnormal circumstances, if no heartbeat request is sent for over 10 seconds, all orders under the account will be automatically canceled; after automatic cancellation, the Deadman Switch mechanism will automatically close.
- To manually close the Deadman Switch mechanism, simply set the countdown to 0. P.S. This interface only supports canceling orders under UTA accounts, not classic accounts.

Please contact your dedicated Business Development representative to apply for access to this interface.

### HTTP Request

- POST /api/v3/trade/countdown-cancel-all
- Rate limit: 1/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/trade/countdown-cancel-all" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{"countdown":"40"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| countdown | String | Yes | Reconnect Window<br> - Unit: seconds<br> - Positive integer, range: [5, 60]. The minimum countdown is 5 second, and the maximum is 60 seconds.<br>Filling in 0 cancels the countdown order cancellation function. |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1728625799912,
  "data": "success"
}
```

### Response Parameters

N/A

[Source](https://www.bitget.com/api-doc/uta/trade/CountDown-Cancel-All)


---

# Get Order Details

### Description

Query order details using either orderId or clientOid.

### HTTP Request

- GET /api/v3/trade/order-info
- Rate limit: 20/sec/UID
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/order-info?orderId=1233965375251996672" \
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
| orderId | String | No | Order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| clientOid | String | No | Client order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730369201783,
  "data": {
    "orderId": "111111111111111111",
    "clientOid": "111111111111111111",
    "category": "SPOT",
    "symbol": "ETHUSDT",
    "orderType": "market",
    "side": "buy",
    "price": "0",
    "qty": "0",
    "amount": "100",
    "cumExecQty": "0.0372",
    "cumExecValue": "99.853356",
    "avgPrice": "2684.23",
    "timeInForce": "gtc",
    "orderStatus": "filled",
    "posSide": "",
    "holdMode": "",
    "tradeSide": "open",
    "reduceOnly": "NO",
    "marginMode": "crossed",
    "stpMode": "none",
    "takeProfit": "",
    "stopLoss": "",
    "tpTriggerBy": "",
    "slTriggerBy": "",
    "tpOrderType": "",
    "slOrderType": "",
    "tpLimitPrice": "",
    "slLimitPrice": "",
    "feeDetail": [
      {
        "feeCoin": "ETH",
        "fee": "0.00000744"
      }
    ],
    "cancelReason": "",
    "execType": "",
    "createdTime": "1730295766596",
    "updatedTime": "1730295766691"
  }
}
```

### Response Parameters

| Return Field | Parameter Type | Description |
| --- | --- | --- |
| orderId | String | Order ID |
| clientOid | String | Client order ID |
| category | String | Product type<br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Symbol name |
| price | String | Order price |
| qty | String | Order quantity<br>*The unit is base coin* |
| amount | String | Order amount<br>*The unit is quote coin* |
| orderType | String | Order type<br>`limit`/`market` |
| cumExecQty | String | Cumulative executed quantity<br>*The unit is base coin* |
| cumExecValue | String | Cumulative executed value<br>*The unit is quote coin* |
| avgPrice | String | Average executed price |
| orderStatus | String | Order status<br>`live` Order created. <br>`new` Order matching. <br>`partially_filled` Partially filled<br>`filled` Fully filled<br>`cancelled` Cancelled |
| side | String | Order side<br>`buy`/`sell` |
| timeInForce | String | Time in force<br>`ioc` Immediate or cancel<br>`fok`: Fill or kill<br>`gtc`: Good 'til canceled<br>`post_only`: Post (Maker) only<br>`rpi`: Retail Price Improvement order |
| posSide | String | Position side<br>`long`/`short` |
| tradeSide | String | Trade side <br>`open`/`close` <br>Detailed enumerations can be obtained on the Enumeration page. |
| holdMode | String | Position mode<br>`one_way_mode`/`hedge_mode` |
| marginMode | String | Margin mode<br>`crossed`: Cross margin<br>`isolated`: Isolated margin<br>Available only for futures |
| stpMode | String | STP Mode(Self Trade Prevention)<br> `none`: not setting STP(default)<br>`cancel_taker`: cancel taker order <br>`cancel_maker`: cancel maker order <br>`cancel_both`: cancel both of taker and maker orders |
| takeProfit | String | Take-Profit Trigger Price |
| stopLoss | String | Stop-Loss Trigger Price |
| tpTriggerBy | String | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| slTriggerBy | String | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| tpOrderType | String | Take-Profit Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| slOrderType | String | Stop-Loss Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| tpLimitPrice | String | Take-Profit Limit Order Execution Price |
| slLimitPrice | String | Stop-Loss Limit Order Execution Price |
| reduceOnly | String | Reduce-only identifier<br>`YES`/`NO` <br>Available only for futures |
| feeDetail | Array | Fee detail |
| > feeCoin | String | Fee coin |
| > fee | String | Total fee |
| cancelReason | String | Cancel reason<br>`normal_cancel` <br>Detailed enumerations can be obtained on the Enumeration page. |
| execType | String | Execution type<br>`normal`Normal <br>`offset` Netting of hedged positions <br>`reduce` Forced reduction<br>`liquidation` Liquidation<br>`delivery` Delivery |
| createdTime | String | Created timestamp<br>A Unix millisecond timestamp. |
| updatedTime | String | Updated timestamp<br>A Unix millisecond timestamp. |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Order-Details)


---

# Get Open Orders

### Description

Query unfilled or partially filled orders. To query closed orders, please use the order history endpoint.

- **Order Limit**
  - **Futures**: 400 orders across all USDT, Coin-M, and USDC futures trading pairs.
  - **Spot**: 400 orders across all spot and margin trading pairs.

### HTTP Request

- GET /api/v3/trade/unfilled-orders
- Rate limit: 20/sec/UID
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/unfilled-orders?category=USDT-FUTURES&symbol=BTCUSDT" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" 
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| category | String | No | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |
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
        "orderType": "limit",
        "side": "buy",
        "price": "45000",
        "qty": "0.01",
        "amount": "0",
        "cumExecQty": "0",
        "cumExecValue": "0",
        "avgPrice": "0",
        "timeInForce": "gtc",
        "orderStatus": "live",
        "posSide": "long",
        "holdMode": "hedge_mode",
        "delegateType": "normal",
        "reduceOnly": "NO",
        "marginMode": "crossed",
        "stpMode": "none",
        "takeProfit": "",
        "stopLoss": "",
        "tpTriggerBy": "",
        "slTriggerBy": "",
        "tpOrderType": "",
        "slOrderType": "",
        "tpLimitPrice": "",
        "slLimitPrice": "",
        "feeDetail": [
          {
            "feeCoin": null,
            "fee": null
          }
        ],
        "createdTime": "1730186725663",
        "updatedTime": "1730186725691"
      }
    ],
    "cursor": "1235058132196622336"
  }
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| list | Array | Order list |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >category | String | Business Line<br>`SPOT`: Spot trading<br>`MARGIN`: Margin trading<br>`USDT-FUTURES`: USDT futures<br>`COIN-FUTURES`: Coin-M futures <br>`USDC-FUTURES`: USDC futures |
| >symbol | String | Symbol name<br>e.g.,`BTCUSDT` |
| >price | String | Order price |
| >qty | String | Order quantity <br>*The unit is base coin* |
| >amount | String | Order amount <br>*The unit is quote coin* <br> Applicable to Spot and Margin. |
| >orderType | String | Order type<br>`limit`/`market` |
| >cumExecQty | String | Cumulative executed order quantity<br> *The unit is base coin* |
| >cumExecValue | String | Cumulative executed order value<br> *The unit is quote coin* |
| >avgPrice | String | Average price |
| >orderStatus | String | Order status<br>`live` Order created. <br>`new` Order matching. <br>`partially_filled` Partially filled<br> |
| >side | String | Order side<br>`buy`/`sell` |
| >timeInForce | String | Time in force<br>`ioc` Immediate or cancel. It must be executed immediately, with any unfilled portion canceled. <br>`fok` Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br>`gtc` Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`post_only` Post only. It will only be added to the order book as a maker.<br>`rpi` Retail Price Improvement order |
| >posSide | String | Position side<br>`long`/`short` |
| >holdMode | String | Holding mode<br>`one_way_mode`/`hedge_mode` |
| >delegateType | String | Delegate type<br>`normal`: Limit Order<br> `stop_profit_market`: Take Profit Market<br> `stop_loss_market`: Stop Loss Market<br> `stop_profit_chase`: Take Profit Chase Order<br> `stop_loss_chase`: Stop Loss Chase Order<br> `trader_delegate`: Lead Trader Order<br> `trader_stop_profit`: Lead Trader Take Profit<br> `trader_stop_loss`: Lead Trader Stop Loss<br> `follower_delegate`: Follower Order<br> `reduce_offset_delegate`: Reduce-Only Offset Order<br> `market`: Market Order<br> `market_risk`: Market Order (Risk Handling)<br> `plan_limit`: Limit Conditional Order<br> `plan_market`: Market Conditional Order<br> `back_contract`: Reverse Position<br> `trader_back_contract`: Lead Trader Reverse Position<br> `strategy_grid_positive`: Strategy - Long Grid<br> `strategy_grid_reverse`: Strategy - Short Grid<br> `strategy_unlimited`: Unlimited Strategy<br> `stop_profit_limit`: Take Profit Limit<br> `stop_loss_limit`: Stop Loss Limit<br> `move_stop_limit`: Trailing Stop Limit<br> `move_stop_market`: Trailing Stop Market<br> `position_stop_profit_limit`: Position Take Profit Limit<br> `position_stop_profit_market`: Position Take Profit Market<br> `position_stop_loss_limit`: Position Stop Loss Limit<br> `position_stop_loss_market`: Position Stop Loss Market<br> `tracking_plan_limit`: Trailing Limit Order<br> `tracking_plan_market`: Trailing Market Order<br> `delivery_close_long`: Long Delivery Close<br> `delivery_close_short`: Short Delivery Close<br> `liquidation`: Liquidation<br> `strategy_dca_positive`: DCA Strategy - Long<br> `strategy_dca_reverse`: DCA Strategy - Short<br> `spot_trace_trader_buy`: Spot Lead Trader Buy<br> `spot_trace_follower_buy`: Spot Follower Buy<br> `spot_trace_trader_sell`: Spot Lead Trader Sell<br> `spot_trace_follower_sell`: Spot Follower Sell<br> `strategy_oco_limit`: Strategy - OCO Limit Order<br> `strategy_oco_trigger`: Strategy - OCO Trigger Order<br> `modify_limit_order`: Modify Limit Order<br> `strategy_regular_buy`: Strategy - Auto-Invest Buy<br> `strategy_grid_middle`: Strategy - Neutral Grid<br> `strategy_cta_positive`: CTA Strategy - Long<br> `strategy_cta_reverse`: CTA Strategy - Short<br> `strategy_tpsl_limit`: Spot TP/SL Limit Order<br> `strategy_tpsl_market`: Spot TP/SL Market Order<br> `strategy_contract_ai`: Futures AI Investment Strategy<br> `strategy_trace_market`: Trailing Stop Market Order<br> `strategy_trace_limit`: Trailing Stop Limit Order<br> `strategy_portfolio_buy`: Strategy - Smart Portfolio Buy<br> `strategy_portfolio_sell`: Strategy - Smart Portfolio Sell<br> `strategy_tradingview`: TradingView Signal Strategy<br> `sigan_trace`: Signal Follower<br> `mmr_stop_loss_market`: MMR Stop Loss Market<br> `bbo_opponent1`: BBO - Opponent Best Price 1<br> `bbo_opponent5`: BBO - Opponent Best Price 5<br> `bbo_companion1`: BBO - Companion Best Price 1<br> `bbo_companion5`: BBO - Companion Best Price 5<br> `bbo_opponent1_profit`: BBO - Opponent 1 Take Profit<br> `bbo_opponent5_profit`: BBO - Opponent 5 Take Profit<br> `bbo_companion1_profit`: BBO - Companion 1 Take Profit<br> `bbo_companion5_profit`: BBO - Companion 5 Take Profit<br> `bbo_opponent1_loss`: BBO - Opponent 1 Stop Loss<br> `bbo_opponent5_loss`: BBO - Opponent 5 Stop Loss<br> `bbo_companion1_loss`: BBO - Companion 1 Stop Loss<br> `bbo_companion5_loss`: BBO - Companion 5 Stop Loss<br> `spot_bbo_opponent1_tpsl`: Spot BBO - Opponent 1 TP/SL<br> `spot_bbo_opponent5_tpsl`: Spot BBO - Opponent 5 TP/SL<br> `spot_bbo_companion1_tpsl`: Spot BBO - Companion 1 TP/SL<br> `spot_bbo_companion5_tpsl`: Spot BBO - Companion 5 TP/SL<br> `dummy_bbo_profit`: BBO - Take Profit<br> `dummy_bbo_loss`: BBO - Stop Loss<br> `strategy_pre_tpsl_limit`: Spot Preset TP/SL Limit Order<br> `strategy_pre_tpsl_market`: Spot Preset TP/SL Market Order<br> `future_signal_delegate`: Futures Copy Trading Limit Order<br> `grant_market`: Voucher Opening Order<br> `tg_signal_limit`: TG Signal Limit Order<br> `tg_signal_tp_market`: TG Signal Take Profit Market<br> `tg_signal_sl_market`: TG Signal Stop Loss Market<br> `strategy_preset_tpsl_limit`: Spot Preset Trigger TP/SL Limit Order<br> `strategy_preset_tpsl_market`: Spot Preset Trigger TP/SL Market Order<br> `trader_iceberg_limit`: Iceberg Order<br> `trader_time_share_market`: TWAP Market Order<br> `strategy_arbitrage_positive`: Funding Rate Arbitrage Strategy - Long<br> `strategy_arbitrage_reverse`: Funding Rate Arbitrage Strategy - Short<br> `liquidation_take_over_long`: Liquidation Takeover Long<br> `liquidation_take_over_short`: Liquidation Takeover Short<br> `convert_hedging`: Convert Hedging<br> `off_close`: Delisting Close<br> |
| >marginMode | String | Margin mode<br>`crossed`: Cross margin<br>`isolated`: Isolated margin<br>Available only for futures |
| >stpMode | String | STP Mode(Self Trade Prevention)<br> `none`: not setting STP(default)<br>`cancel_taker`: cancel taker order <br>`cancel_maker`: cancel maker order <br>`cancel_both`: cancel both of taker and maker orders |
| >takeProfit | String | Take-Profit Trigger Price |
| >stopLoss | String | Stop-Loss Trigger Price |
| >tpTriggerBy | String | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| >slTriggerBy | String | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| >tpOrderType | String | Take-Profit Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| >slOrderType | String | Stop-Loss Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| >tpLimitPrice | String | Take-Profit Limit Order Execution Price |
| >slLimitPrice | String | Stop-Loss Limit Order Execution Price |
| >reduceOnly | String | Reduce-only identifier <br>`YES`/`NO` |
| >feeDetail | Array | Fee detail list |
| >> feeCoin | String | Fee coin |
| >> fee | String | Total fee |
| >createdTime | String | Order created timestamp<br>A Unix millisecond timestamp |
| >updatedTime | String | Order update timestamp<br>A Unix millisecond timestamp |
| cursor | String | Cursor for the next page (the smallest orderId in current page; pass it to query older orders) |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Order-Pending)


---

# Get Order History

### Description

Query historical orders within the last 90 days.

- **Query Range Constraint** Each individual query can only cover a maximum of 30 days within the 90-day window.

### HTTP Request

- GET /api/v3/trade/history-orders
- Rate limit: 20/sec/UID
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/history-orders?category=USDT-FUTURES" \
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
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |
| startTime | String | No | Start timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085`<br>The access window is 90 days |
| endTime | String | No | End timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383185`<br> The time range between `startTime` and `endTime` must not exceed 30 days |
| limit | String | No | Limit per page <br>Default:`100`. Maximum:`100` |
| cursor | String | No | Cursor <br>Pagination is implemented by omitting the cursor in the first query and applying the cursor from the previous query for subsequent pages |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730186348272,
  "data": {
    "list": [
      {
        "orderId": "111111111111111111",
        "clientOid": "111111111111111111",
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "orderType": "limit",
        "side": "sell",
        "price": "49534.4",
        "qty": "0.429",
        "amount": "0",
        "cumExecQty": "0.429",
        "cumExecValue": "21250.2929",
        "avgPrice": "49534.4",
        "timeInForce": "gtc",
        "orderStatus": "filled",
        "posSide": "long",
        "holdMode": "hedge_mode",
        "delegateType": "normal",
        "reduceOnly": "NO",
        "marginMode": "crossed",
        "stpMode": "none",
        "takeProfit": "",
        "stopLoss": "",
        "tpTriggerBy": "",
        "slTriggerBy": "",
        "tpOrderType": "",
        "slOrderType": "",
        "tpLimitPrice": "",
        "slLimitPrice": "",
        "feeDetail": [
          {
            "feeCoin": "USDT",
            "fee": "4.2500586"
          }
        ],
        "cancelReason": "normal_cancel",
        "execType": "liquidation",
        "createdTime": "1730181468493",
        "updatedTime": "1730181468493"
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
| >clientOid | String | Client order id |
| >category | String | Product Type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name <br>e.g.,`BTCUSDT` |
| >price | String | Order price |
| >qty | String | Order quantity <br>*The unit is base coin* |
| >amount | String | Order amount <br>*The unit is quote coin* <br> Applicable to Spot and Margin. |
| >orderType | String | Order type <br>`limit`/`market` |
| >cumExecQty | String | Cumulative executed order quantity<br> *The unit is base coin* |
| >cumExecValue | String | Cumulative executed order value<br> *The unit is quote coin* |
| >avgPrice | String | Average price |
| >orderStatus | String | Order status<br>`live` Order created. <br>`new` Order matching. <br>`partially_filled` Partially filled<br>`filled` Fully filled<br>`cancelled` Cancelled |
| >side | String | Order side<br>`buy`/`sell` |
| >timeInForce | String | Time in force<br>`ioc` Immediate or cancel. It must be executed immediately, with any unfilled portion canceled. <br>`fok` Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br>`gtc` Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`post_only` Post only. It will only be added to the order book as a maker.<br>`rpi` Retail Price Improvement order |
| >posSide | String | Position side<br>`long`/`short` |
| >holdMode | String | Holding mode<br>`one_way_mode`/`hedge_mode` |
| >delegateType | String | Delegate type<br>`normal`: Limit Order<br> `stop_profit_market`: Take Profit Market<br> `stop_loss_market`: Stop Loss Market<br> `stop_profit_chase`: Take Profit Chase Order<br> `stop_loss_chase`: Stop Loss Chase Order<br> `trader_delegate`: Lead Trader Order<br> `trader_stop_profit`: Lead Trader Take Profit<br> `trader_stop_loss`: Lead Trader Stop Loss<br> `follower_delegate`: Follower Order<br> `reduce_offset_delegate`: Reduce-Only Offset Order<br> `market`: Market Order<br> `market_risk`: Market Order (Risk Handling)<br> `plan_limit`: Limit Conditional Order<br> `plan_market`: Market Conditional Order<br> `back_contract`: Reverse Position<br> `trader_back_contract`: Lead Trader Reverse Position<br> `strategy_grid_positive`: Strategy - Long Grid<br> `strategy_grid_reverse`: Strategy - Short Grid<br> `strategy_unlimited`: Unlimited Strategy<br> `stop_profit_limit`: Take Profit Limit<br> `stop_loss_limit`: Stop Loss Limit<br> `move_stop_limit`: Trailing Stop Limit<br> `move_stop_market`: Trailing Stop Market<br> `position_stop_profit_limit`: Position Take Profit Limit<br> `position_stop_profit_market`: Position Take Profit Market<br> `position_stop_loss_limit`: Position Stop Loss Limit<br> `position_stop_loss_market`: Position Stop Loss Market<br> `tracking_plan_limit`: Trailing Limit Order<br> `tracking_plan_market`: Trailing Market Order<br> `delivery_close_long`: Long Delivery Close<br> `delivery_close_short`: Short Delivery Close<br> `liquidation`: Liquidation<br> `strategy_dca_positive`: DCA Strategy - Long<br> `strategy_dca_reverse`: DCA Strategy - Short<br> `spot_trace_trader_buy`: Spot Lead Trader Buy<br> `spot_trace_follower_buy`: Spot Follower Buy<br> `spot_trace_trader_sell`: Spot Lead Trader Sell<br> `spot_trace_follower_sell`: Spot Follower Sell<br> `strategy_oco_limit`: Strategy - OCO Limit Order<br> `strategy_oco_trigger`: Strategy - OCO Trigger Order<br> `modify_limit_order`: Modify Limit Order<br> `strategy_regular_buy`: Strategy - Auto-Invest Buy<br> `strategy_grid_middle`: Strategy - Neutral Grid<br> `strategy_cta_positive`: CTA Strategy - Long<br> `strategy_cta_reverse`: CTA Strategy - Short<br> `strategy_tpsl_limit`: Spot TP/SL Limit Order<br> `strategy_tpsl_market`: Spot TP/SL Market Order<br> `strategy_contract_ai`: Futures AI Investment Strategy<br> `strategy_trace_market`: Trailing Stop Market Order<br> `strategy_trace_limit`: Trailing Stop Limit Order<br> `strategy_portfolio_buy`: Strategy - Smart Portfolio Buy<br> `strategy_portfolio_sell`: Strategy - Smart Portfolio Sell<br> `strategy_tradingview`: TradingView Signal Strategy<br> `sigan_trace`: Signal Follower<br> `mmr_stop_loss_market`: MMR Stop Loss Market<br> `bbo_opponent1`: BBO - Opponent Best Price 1<br> `bbo_opponent5`: BBO - Opponent Best Price 5<br> `bbo_companion1`: BBO - Companion Best Price 1<br> `bbo_companion5`: BBO - Companion Best Price 5<br> `bbo_opponent1_profit`: BBO - Opponent 1 Take Profit<br> `bbo_opponent5_profit`: BBO - Opponent 5 Take Profit<br> `bbo_companion1_profit`: BBO - Companion 1 Take Profit<br> `bbo_companion5_profit`: BBO - Companion 5 Take Profit<br> `bbo_opponent1_loss`: BBO - Opponent 1 Stop Loss<br> `bbo_opponent5_loss`: BBO - Opponent 5 Stop Loss<br> `bbo_companion1_loss`: BBO - Companion 1 Stop Loss<br> `bbo_companion5_loss`: BBO - Companion 5 Stop Loss<br> `spot_bbo_opponent1_tpsl`: Spot BBO - Opponent 1 TP/SL<br> `spot_bbo_opponent5_tpsl`: Spot BBO - Opponent 5 TP/SL<br> `spot_bbo_companion1_tpsl`: Spot BBO - Companion 1 TP/SL<br> `spot_bbo_companion5_tpsl`: Spot BBO - Companion 5 TP/SL<br> `dummy_bbo_profit`: BBO - Take Profit<br> `dummy_bbo_loss`: BBO - Stop Loss<br> `strategy_pre_tpsl_limit`: Spot Preset TP/SL Limit Order<br> `strategy_pre_tpsl_market`: Spot Preset TP/SL Market Order<br> `future_signal_delegate`: Futures Copy Trading Limit Order<br> `grant_market`: Voucher Opening Order<br> `tg_signal_limit`: TG Signal Limit Order<br> `tg_signal_tp_market`: TG Signal Take Profit Market<br> `tg_signal_sl_market`: TG Signal Stop Loss Market<br> `strategy_preset_tpsl_limit`: Spot Preset Trigger TP/SL Limit Order<br> `strategy_preset_tpsl_market`: Spot Preset Trigger TP/SL Market Order<br> `trader_iceberg_limit`: Iceberg Order<br> `trader_time_share_market`: TWAP Market Order<br> `strategy_arbitrage_positive`: Funding Rate Arbitrage Strategy - Long<br> `strategy_arbitrage_reverse`: Funding Rate Arbitrage Strategy - Short<br> `liquidation_take_over_long`: Liquidation Takeover Long<br> `liquidation_take_over_short`: Liquidation Takeover Short<br> `convert_hedging`: Convert Hedging<br> `off_close`: Delisting Close<br> |
| >reduceOnly | String | Reduce-only identifier<br>`yes`/`no` <br>`yes` indicates that your position may only be reduced in size upon the activation of this order |
| >feeDetail | Array | Fee detail |
| >>feeCoin | String | Fee coin |
| >>fee | String | Fee |
| >cancelReason | String | Cancel reason<br>`normal_cancel` |
| >execType | String | Execution type<br>`normal` Normal<br>`offset` Offset<br> `reduce` Forced reduction <br> `liquidation` Liquidation<br>`delivery` Delivery |
| >marginMode | String | Margin mode<br>`crossed`: Cross margin<br>`isolated`: Isolated margin<br>Available only for futures |
| >stpMode | String | STP Mode(Self Trade Prevention)<br> `none`: not setting STP(default)<br>`cancel_taker`: cancel taker order <br>`cancel_maker`: cancel maker order <br>`cancel_both`: cancel both of taker and maker orders |
| >takeProfit | String | Take-Profit Trigger Price |
| >stopLoss | String | Stop-Loss Trigger Price |
| >tpTriggerBy | String | Take-Profit Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| >slTriggerBy | String | Stop-Loss Trigger Type<br>`market`: Market Price<br>`mark`: Mark Price |
| >tpOrderType | String | Take-Profit Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| >slOrderType | String | Stop-Loss Order Type<br>`limit`: Limit Order<br>`market`: Market Order |
| >tpLimitPrice | String | Take-Profit Limit Order Execution Price |
| >slLimitPrice | String | Stop-Loss Limit Order Execution Price |
| >createdTime | String | Created timestamp<br>A Unix millisecond timestamp. e.g.,`1736388000` |
| >updatedTime | String | Updated timestamp<br>A Unix millisecond timestamp. e.g.,`1736388000` |
| cursor | String | Cursor |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Order-History)


---

# Get Fill History

### Description

Query historical fills within the last 90 days.

- **Query Range Constraint** Each individual query can only cover a maximum of 30 days within the 90-day window.

### HTTP Request

- GET /api/v3/trade/fills
- Rate limit: 20/sec/UID
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/trade/fills?orderId=111111111111111111" \
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
| category | String | No | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| orderId | String | No | Order ID |
| startTime | String | No | Start timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085`<br>The access window is 90 days. |
| endTime | String | No | End timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383185`<br> The time range between `startTime` and `endTime` must not exceed 30 days. |
| limit | String | No | Limit per page <br>Default:`100`. Maximum:`100` |
| cursor | String | No | Cursor <br>Pagination is implemented by omitting the cursor in the first query and applying the cursor from the previous query for subsequent pages |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1750142995229,
  "data": {
    "list": [
      {
        "execId": "131111111111111111",
        "execLinkId": "131111111111111111",
        "orderId": "131111111111111111",
        "clientOid": "131111111111111111",
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "orderType": "market",
        "side": "sell",
        "execPrice": "106950.1",
        "execQty": "0.01",
        "execValue": "1069.501",
        "tradeScope": "taker",
        "tradeSide": "open",
        "feeDetail": [
          {
            "feeCoin": "USDT",
            "fee": "0.6417006"
          }
        ],
        "createdTime": "1750141421721",
        "updatedTime": "1750141421728",
        "execPnl": "-0.002",
        "isRPI": "no"
      }
    ],
    "cursor": "131111111111111111"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | Fill list |
| >execId | String | Fill ID |
| >execLinkId | String | Execution correlation ID |
| >orderId | String | Order ID |
| >clientOid | String | Client order ID |
| >category | String | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name |
| >orderType | String | Order type <br>`limit`/`market` |
| >execPrice | String | Fill price |
| >execQty | String | Fill quantity<br>*The unit is base coin* |
| >execValue | String | Fill value<br>*The unit is quote coin* |
| >feeDetail | Array | Fee detail |
| >>feeCoin | String | Fee coin |
| >>fee | String | Fee |
| >side | String | Fill side<br>`buy`/`sell` |
| >tradeScope | String | Trade scope<br>`taker`/`maker` |
| >tradeSide | String | Trade side <br>`open`/`close` <br>Detailed enumerations can be obtained on the Enumeration page. |
| >createdTime | String | Created timestamp<br>A Unix millisecond timestamp. e.g.,`1736388000` |
| >updatedTime | String | Updated timestamp<br>A Unix millisecond timestamp. e.g.,`1736388000` |
| >execPnl | String | Closed Position Profit and Loss |
| >isRPI | String | Whether it is an RPI fill<br>`yes` Yes<br>`no` No |
| cursor | String | Cursor |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Order-Fills)


---

# Get Position Info

### Description

Query real-time position data by symbol, side, or category.

### HTTP Request

- GET /api/v3/position/current-position
- Rate limit: 20/sec/UID
- Permission: UTA trade (read)
Request

```
curl "https://api.bitget.com/api/v3/position/current-position?category=USDT-FUTURES&symbol=BTCUSDT" \
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
| symbol | String | No | Symbol name<br>e.g.`BTCUSDT`<br>If no symbol is provided, all positions in the corresponding category will be returned. |
| posSide | String | No | Position side <br>`long`/`short`<br> If this field is provided, only the position in the corresponding side will be returned. |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1753103840140,
  "data": {
    "list": [
      {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "marginCoin": "USDT",
        "holdMode": "hedge_mode",
        "posSide": "long",
        "marginMode": "crossed",
        "positionBalance": "4701531.84941582",
        "available": "119.2068",
        "frozen": "0",
        "total": "119.2068",
        "leverage": "3",
        "curRealisedPnl": "0",
        "avgPrice": "108674",
        "positionStatus": "normal",
        "unrealisedPnl": "1124573.04243999",
        "liquidationPrice": "43099.9",
        "mmr": "0.015",
        "profitRate": "0.2391929010498401",
        "markPrice": "118097",
        "breakEvenPrice": "109208.6",
        "totalFunding": "-53076.32433032",
        "openFeeTotal": "-2842.86603479",
        "closeFeeTotal": "0",
        "createdTime": "1736378720620",
        "updatedTime": "1753102803148"
      }
    ]
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | Position list |
| >category | String | Product Type <br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name |
| >marginCoin | String | Margin coin |
| >posSide | String | Position side <br>`long`/`short` |
| >positionBalance | String | Position balance (margin amount)<br>*The unit is margin coin*<br>In isolated margin mode, reflects the isolated margin amount for this position |
| >available | String | Available position |
| >frozen | String | Frozen position |
| >total | String | Total position (available + frozen) |
| >leverage | String | Leverage multiple |
| >curRealisedPnl | String | Current realised profit and loss |
| >avgPrice | String | Average entry price |
| >marginMode | String | Margin mode<br>`crossed` crossed margin<br>`isolated` isolated margin |
| >positionStatus | String | Position status<br>`normal` |
| >holdMode | String | Holding mode<br>`one_way_mode`/`hedge_mode` |
| >unrealisedPnl | String | Unrealised profit and loss<br>In isolated margin mode, reflects the unrealised PnL for this isolated position |
| >liquidationPrice | String | Estimated liquidation price<br>Less than or equal to 0 means liquidation will not occur |
| >mmr | String | Maintenance margin rate |
| >profitRate | String | Profit rate |
| >markPrice | String | Mark price |
| >breakEvenPrice | String | Break-even price |
| >totalFunding | String | Total funding<br>The accumulated fund fee during the position's duration. If the value is zero, it indicates no fees have been charged |
| >openFeeTotal | String | Fees deducted on position opening <br>Opening fees deducted during the position's lifetime |
| >closeFeeTotal | String | Fees deducted on position closing <br>Closing fees deducted during the position's lifetime |
| >createdTime | String | Created timestamp<br> A Unix millisecond timestamp |
| >updatedTime | String | Updated timestamp<br> A Unix millisecond timestamp |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Position)


---

# Get Positions History

### Description

Query historical positions within the last 90 days.

### HTTP Request

- GET /api/v3/position/history-position
- Rate limit: 20/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl "https://api.bitget.com/api/v3/position/history-position?category=USDT-FUTURES" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" 
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type<br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name<br>e.g.,`BTCUSDT` |
| startTime | String | No | Start timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085`<br>The access window is 90 days |
| endTime | String | No | End timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383185`<br> The time range between `startTime` and `endTime` must not exceed 30 days |
| limit | String | No | Limit per page<br>Default:`100`. Maximum:`100` |
| cursor | String | No | Cursor <br>Pagination is implemented by omitting the cursor in the first query and applying the cursor from the previous query for subsequent pages |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730186957802,
  "data": {
    "list": [
      {
        "positionId": "1111111111111111111",
        "category": "USDT-FUTURES",
        "symbol": "EOSUSDT",
        "marginCoin": "USDT",
        "holdMode": "one_way_mode",
        "posSide": "long",
        "marginMode": "crossed",
        "openPriceAvg": "1960.001",
        "closePriceAvg": "1959.999",
        "openTotalPos": "58",
        "closeTotalPos": "58",
        "cumRealisedPnl": "-0.116",
        "netProfit": "-45.588",
        "totalFunding": "0",
        "openFeeTotal": "-22.7360116",
        "closeFeeTotal": "-22.7359884",
        "createdTime": "1729928018076",
        "updatedTime": "1729929656321"
      }
    ],
    "cursor": "1111111111111111111"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | List |
| >positionId | String | Position ID |
| >category | String | Product Type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name |
| >marginCoin | String | Margin coin |
| >posSide | String | Position side<br>`long`/`short` |
| >openPriceAvg | String | Average opening price |
| >closePriceAvg | String | Average closing price |
| >openTotalPos | String | Total open position |
| >closeTotalPos | String | Total closed position |
| >marginMode | String | Margin mode<br>`crossed`/`isolated` |
| >holdMode | String | Holding mode<br>`one_way_mode` /`hedge_mode` |
| >cumRealisedPnl | String | Cumulative realised profit and loss<br>Excluding fees and funding costs |
| >netProfit | String | Net profit and loss<br>Including fees and funding costs |
| >totalFunding | String | Total funding<br>The accumulated fund fee during the position's duration. If the value is zero, it indicates no fees have been charged |
| >openFeeTotal | String | Fees deducted on position opening <br>Opening fees deducted during the position's lifetime |
| >closeFeeTotal | String | Fees deducted on position closing <br>Closing fees deducted during the position's lifetime |
| >createdTime | String | Position created timestamp <br> A Unix millisecond timestamp |
| >updatedTime | String | Position updated timestamp <br> A Unix millisecond timestamp |
| cursor | String | Cursor |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Position-History)


---

# Get Position ADL Rank

### Description

Get Position ADL Rank

### HTTP Request

- GET /api/v3/position/adlRank
- Rate limit: 1/sec/UID
- Permission: UTA trade (read & write)
Request

```
curl "https://api.bitget.com/api/v3/position/adlRank" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json"
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1754035547922,
  "data": [
    {
      "symbol": "MOVEUSDT",
      "marginCoin": "USDT",
      "adlRank": "0.4872",
      "holdSide": "long"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| symbol | String | Symbol |
| marginCoin | String | Margin Coin |
| adlRank | String | ADL Ranking<br>The ranking of your current position in the auto-deleveraging sequence. When an auto-deleveraging event occurs in the market, the closer the value is to 1, the higher the probability that your position will be reduced |
| holdSide | String | Position Direction <br>`long` long position <br>`short` short position |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Position-ADL-Rank)


---

# Get Max Open Available

### Description

Get max open available

### HTTP Request

- POST /api/v3/account/max-open-available
- Rate limit: 5/sec/UID
- Permission: UTA trade (read)
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/max-open-available" \
  -H "ACCESS-KEY:your apiKey" \
  -H "ACCESS-SIGN:*" \
  -H "ACCESS-PASSPHRASE:*" \
  -H "ACCESS-TIMESTAMP:1659076670000" \
  -H "locale:zh-CN" \
  -H "Content-Type: application/json" \
  -d '{"category":"SPOT","symbol":"BTCUSDT","orderType":"market","side":"sell"}' 
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| orderType | String | Yes | Order type<br>`limit`/`market` |
| side | String | Yes | Transaction direction `buy`/`sell` |
| price | String | No | Order price <br>This field is required when orderType is `limit` |
| size | String | No | Order quantity, base coin |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1741851607871,
  "data": {
    "available": "52.008255",
    "maxOpen": "",
    "buyOpenCost": "",
    "sellOpenCost": "",
    "maxBuyOpen": "",
    "maxSellOpen": "",
    "maxBuyAvailable": "",
    "maxSellAvailable": ""
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| available | String | Available <br> For spot/margin When `side=buy`, it represents the number of quote coins; when `side=sell`, it represents the number of base coins. <br> The futures represents the quantity of the quote coins |
| maxOpen | String | Maximum openable size <br> When `side=buy`, it represents the number of quote coins; when `side=sell`, it represents the number of base coins. <br> Only the margin has a value. |
| buyOpenCost | String | When buying, the quantity of the quote coin required to open a position is calculated based on the input `size`. <br>Only the futures has a value. |
| sellOpenCost | String | When selling, the quantity of the quote coin required to open a position is calculated based on the input `size`.<br>Only the futures has a value. |
| maxBuyOpen | String | The maximum position that can be opened for purchase. <br> Base coin quantity calculated based on account balance.<br> Only the futures has a value. |
| maxSellOpen | String | The maximum position that can be opened for sale. <br> Base coin quantity calculated based on account balance. <br> Only the futures has a value. |
| maxBuyAvailable | String | Maximum buy available <br> |
| maxSellAvailable | String | Maximum sell available <br> |

[Source](https://www.bitget.com/api-doc/uta/trade/Get-Max-Open-Available)

