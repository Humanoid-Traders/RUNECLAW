# Bitget UTA API — Account

| Endpoint / Channel | Slug |
| --- | --- |
| [Get Account](#get-account) | `account/Get-Account` |
| [Get Account Info](#get-account-info) | `account/Get-Account-Info` |
| [Get Account Setting](#get-account-setting) | `account/Get-Account-Setting` |
| [Adjust Account Mode](#adjust-account-mode) | `account/Adjust-Account-Mode` |
| [Change Leverage](#change-leverage) | `account/Change-Leverage` |
| [Change Position Mode](#change-position-mode) | `account/Change-Position-Mode` |
| [Get Account Fee Rate](#get-account-fee-rate) | `account/Get-Account-Fee-Rate` |
| [Set Margin](#set-margin) | `account/Set-Margin` |
| [Get Max Transferable](#get-max-transferable) | `account/Get-Max-Transferable` |
| [Get Max Withdrawal](#get-max-withdrawal) | `account/Get-Max-Withdrawal` |
| [Get Financial Records](#get-financial-records) | `account/Get-Financial-Records` |
| [Switch Account](#switch-account) | `account/Switch-Account` |
| [Get Switch Status](#get-switch-status) | `account/Get-Switch-Status` |
| [Get OI Limit](#get-oi-limit) | `account/Get-OI-Limit` |
| [Get Account Funding Assets](#get-account-funding-assets) | `account/Get-Account-Funding-Assets` |


---

# Get Account Assets

### Description

Query account information and assets, with only non-zero balances being returned.

### HTTP Request

- GET /api/v3/account/assets
- Rate limit: 20/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/assets" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" 
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1746687063471,
  "data": {
    "accountEquity": "11.13919278",
    "usdtEquity": "11.13921165",
    "btcEquity": "0.00011256",
    "unrealisedPnl": "0",
    "usdtUnrealisedPnl": "0",
    "btcUnrealizedPnl": "0",
    "effEquity": "6.19299777",
    "mmr": "0",
    "imr": "0",
    "mgnRatio": "0",
    "positionMgnRatio": "0",
    "assets": [
      {
        "coin": "USDT",
        "equity": "6.19300826",
        "usdValue": "6.19299777",
        "balance": "6.19300826",
        "available": "6.19300826",
        "debt": "0",
        "locked": "0"
      },
      {
        "coin": "BGB",
        "equity": "1.15582129",
        "usdValue": "4.94618029",
        "balance": "1.15582129",
        "available": "1.15582129",
        "debt": "0",
        "locked": "0"
      }
    ]
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| accountEquity | String | Account equity (USD) |
| usdtEquity | String | Account equity (USDT) |
| btcEquity | String | Account equity (BTC) |
| unrealisedPnl | String | Unrealised profit and loss (USD) |
| usdtUnrealisedPnl | String | Unrealised profit and loss (USDT) |
| btcUnrealizedPnl | String | Unrealised profit and loss (BTC) |
| effEquity | String | Effective equity (USD)<br> The net value available for margin in spot and perpetual trades under cross-margin mode, converted to fiat |
| mmr | String | Maintenance margin (USD)<br> The minimum margin required to maintain the position, converted to fiat |
| imr | String | Initial margin (USD)<br> Total initial margin of assets in base coin, converted to fiat |
| mgnRatio | String | Margin ratio |
| positionMgnRatio | String | Position MMR |
| assets | Array | Asset list |
| >coin | String | Coin name |
| >equity | String | Coin equity |
| >usdValue | String | Coin equity (USD) |
| >balance | String | Coin balance |
| >debt | String | Debt<br>*Applicable when placing margin orders* |
| >available | String | Available |
| >locked | String | Locked<br>*Applicable when placing spot orders* |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Account)


---

# Get Account Info

### Description

Query account information, including UID, inviter, parent account, channel, IP whitelist, permission type and permissions list.

### HTTP Request

- GET /api/v3/account/info
- Rate limit: 5/sec/UID
- Permission: No permission required
Request

```
curl "https://api.bitget.com/api/v3/account/info" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json"
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1744617600000,
  "data": {
    "userId": "123456789",
    "inviterId": "987654321",
    "parentId": "",
    "channelCode": "6258",
    "channel": "official",
    "ips": "192.168.1.1,192.168.1.2",
    "permType": "read-and-write",
    "permissions": [
      "uta_mgt",
      "uta_trade",
      "withdraw",
      "copy_futures_position",
      "copy_futures_order"
    ],
    "regisTime": "1704067200000"
  }
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| userId | String | User ID |
| inviterId | String | Inviter UID |
| parentId | String | Parent account UID.<br>Only has a value when the calling account is a sub-account. |
| channelCode | String | Channel invitation code |
| channel | String | Channel |
| ips | String | IP whitelist |
| permType | String | Permission type<br>`read-only` Read only<br>`read-and-write` Read and write |
| permissions | Array | Permissions list<br>`uta_mgt` UTA management<br>`uta_trade` UTA trading<br>`withdraw` Withdrawal<br>`copy_futures_position` Futures copy trading position<br>`copy_futures_order` Futures copy trading order |
| regisTime | String | Account registration time (Unix timestamp in milliseconds) |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Account-Info)


---

# Get Account Info

### Description

- Query account information, including the holding mode, margin mode, leverage multiple, and more.

### HTTP Request

- GET /api/v3/account/settings
- Rate limit: 20/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/settings" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" 
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1753787749280,
  "data": {
    "uid": "1111111111",
    "accountMode": "hybrid",
    "assetMode": "multi_assets",
    "accountLevel": "advanced",
    "holdMode": "one_way_mode",
    "stpMode": "none",
    "symbolConfigList": [
      {
        "category": "USDT-FUTURES",
        "symbol": "BGBUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "BMTUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "1000SATSUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "FUNUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "1000BONKUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "marginMode": "crossed",
        "leverage": "1"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "IOTAUSDT",
        "marginMode": "crossed",
        "leverage": "20"
      },
      {
        "category": "USDT-FUTURES",
        "symbol": "C98USDT",
        "marginMode": "crossed",
        "leverage": "20"
      }
    ],
    "coinConfigList": [
      {
        "coin": "USDT",
        "leverage": "6"
      },
      {
        "coin": "SHIB",
        "leverage": "2"
      },
      {
        "coin": "BTC",
        "leverage": "3"
      }
    ]
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| uid | String | UID |
| accountMode | String | Account Mode<br> `unified` Unified Mode <br> `hybrid` Hybrid Mode <br>`upgrading` Unified Account Upgrading <br> `switching` Classic Account Switching |
| accountLevel | String | Account level<br> `basic` Basic mode <br> `advanced` Advanced mode <br> `isolated` Isolated margin mode <br> `delta` Delta-neutral mode |
| assetMode | String | Asset mode<br>`multi_assets` Multi_assets mode |
| holdMode | String | Holding mode<br>`one_way_mode`/`hedge_mode` |
| stpMode | String | STP Mode <br> `none` not setting STP <br> `cancel_taker` cancel taker order <br> `cancel_maker` cancel maker order <br> `cancel_both` cancel both of taker and maker orders |
| symbolConfigList | Array | Symbol configuration list |
| >category | String | Product type <br>`USDT-FUTURES` USDT futures<br>`COIN-FUTURES` Coin-M futures<br>`USDC-FUTURES` USDC futures |
| >symbol | String | Symbol name |
| >marginMode | String | Margin mode <br>`crossed`/`isolated` |
| >leverage | Array | Leverage multiple |
| coinConfigList | Array | Coin configuration list |
| >coin | String | Coin name |
| >leverage | String | Leverage multiple |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Account-Setting)


---

# Adjust Account Mode

### Description

This endpoint supports the following account-mode switching scenarios. It supports switching between Basic Mode and Advanced Mode under the Unified Account:

1. The master account switches its own mode
2. A sub-account switches its own mode
3. The master account switches the mode for its sub-account(s)

### HTTP Request

- POST /api/v3/account/adjust-account-mode
- Rate limit: 1/sec/UID
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/adjust-account-mode" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{"mode":"basic"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| mode | String | Yes | Account mode <br>`basic` Basic mode <br>`advanced` Advanced mode <br>`delta` Delta-neutral mode <br>`isolated` Isolated margin mode |
| targetUid | String | No | Target account UID.<br> If not provided, it defaults to the currently operated account. <br>If a sub-account UID is provided, it indicates the master account is operating on the sub-account. |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1728625799912,
  "data": null
}
```

### Response Parameters

N/A

[Source](https://www.bitget.com/api-doc/uta/account/Adjust-Account-Mode)


---

# Set Leverage

### Description

This endpoint allows you to set leverage.

### HTTP Request

- POST /api/v3/account/set-leverage
- Rate limit: 10/sec/UID
- Permission: UTA mgt. (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/set-leverage" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{"category":"USDT-FUTURES","symbol":"BTCUSDT","leverage":"10"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br>`MARGIN` Margin trading<br>`USDT-FUTURES` USDT futures <br>`COIN-FUTURES`Coin-M futures<br>`USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>This field is required to set leverage for futures |
| leverage | String | Yes | Leverage multiple |
| coin | String | No | Coin name <br>This field is required to set leverage for margin trading |
| posSide | String | No | Position side<br> `long`/`short` <br>This field is required to set leverage for isolated margin |
| marginMode | String | No | Margin mode<br>`crossed` Cross margin<br>`isolated` Isolated margin<br>Defaults to cross margin if not specified. Only futures product lines support isolated margin leverage adjustment in this update. |
| longLeverage | String | No | Long position leverage<br>Only applicable when using isolated margin with two-way position mode and different leverage for each direction.<br>In two-way position mode, if both `leverage` and `longLeverage` are passed, `longLeverage` takes effect and `leverage` is ignored. |
| shortLeverage | String | No | Short position leverage<br>Only applicable when using isolated margin with two-way position mode and different leverage for each direction.<br>In two-way position mode, if both `leverage` and `shortLeverage` are passed, `shortLeverage` takes effect and `leverage` is ignored. |

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

[Source](https://www.bitget.com/api-doc/uta/account/Change-Leverage)


---

# Set Holding Mode

### Description

This endpoint allows you to set the position holding mode between one-way and hedge mode.

### HTTP Request

- POST /api/v3/account/set-hold-mode
- Rate limit: 10/sec/UID
- Permission: UTA mgt. (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/set-hold-mode" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{"holdMode": "one_way_mode"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| holdMode | String | Yes | Holding mode<br>`one_way_mode` This mode allows holding positions in a single direction, either long or short, but not both at the same time <br>`hedge_mode` This mode allows holding both long and short positions simultaneously |

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

[Source](https://www.bitget.com/api-doc/uta/account/Change-Position-Mode)


---

# Get Account Fee Rate

### Description

Get Account Fee Rate

### HTTP Request

- GET /api/v3/account/fee-rate
- Rate limit: 3/sec/UID
- Unified account management read permissions are required
Request

```
curl "https://api.bitget.com/api/v3/account/fee-rate?symbol=BTCUSDT&category=SPOT" \
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
| symbol | String | Yes | Symbol <br>e.g.,`BTCUSDT` |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1751972326323,
  "data": {
    "makerFeeRate": "0.0008",
    "takerFeeRate": "0.0008"
  }
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| makerFeeRate | String | Maker fee rate<br>In decimal form, e.g., 0.0002 represents 0.02% |
| takerFeeRate | String | Taker fee rate<br>In decimal form, e.g., 0.0002 represents 0.02% |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Account-Fee-Rate)


---

# Set Margin

### Description

This endpoint allows you to adjust the margin for an isolated margin position.

### HTTP Request

- POST /api/v3/account/set-margin
- Rate limit: 10/sec/UID
- Permission: UTA mgt. (read & write)
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/set-margin" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{"category":"USDT-FUTURES","symbol":"BTCUSDT","posSide":"long","operation":"add","amount":"10"}'
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type<br>`USDT-FUTURES` USDT futures<br>`COIN-FUTURES` Coin-M futures<br>`USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name |
| posSide | String | Yes | Position side<br>`long` Long position<br>`short` Short position |
| operation | String | Yes | Operation type<br>`add` Add margin<br>`remove` Remove margin |
| amount | String | Yes | Margin adjustment amount, denominated in the margin currency<br>USDT-FUTURES: USDT; USDC-FUTURES: USDC; COIN-FUTURES: base currency |

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

[Source](https://www.bitget.com/api-doc/uta/account/Set-Margin)


---

# Get Max Transferable

### Description

Get the maximum transferable amount for the unified account.

### HTTP Request

- GET /api/v3/account/max-transferable
- Rate limit: 3/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/max-transferable?coin=USDT" \
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
| coin | String | Yes | Coin name |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730189435812,
  "data": {
    "coin": "USDT",
    "maxTransfer": "1",
    "borrowMaxTransfer": "1"
  }
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| coin | String | Coin name |
| maxTransfer | String | Maximum transferable amount<br> unit: coin. |
| borrowMaxTransfer | String | Maximum transferable amount for borrowed coins <br>unit: coin. |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Max-Transferable)


---

# Get Max Withdrawal

### Description

Get the maximum withdrawable amount for the specified coin in the unified account. The amount is calculated in real time based on the account's available balance, open positions, liabilities, and other factors. This can be used to obtain the safe withdrawal limit before initiating a withdrawal to avoid failures due to exceeding the limit.

> Note: The returned result is a real-time calculated value with millisecond-level latency. The platform will perform a secondary validation at the time of actual withdrawal; this interface result is for reference only.

### HTTP Request

- GET /api/v3/account/max-withdrawal
- Rate limit: 10/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/max-withdrawal?coin=USDT" \
   -H "ACCESS-KEY:your apiKey" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json"
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| coin | String | Yes | Coin name, e.g. USDT |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1740000000000,
  "data": {
    "coin": "USDT",
    "otcMaxWithdrawal": "100",
    "spotMaxWithdrawal": "500",
    "utaMaxWithdrawal": "1000",
    "totalMaxWithdrawal": "1600"
  }
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| coin | String | Coin name |
| otcMaxWithdrawal | String | Max withdrawable amount for OTC account |
| spotMaxWithdrawal | String | Max withdrawable amount for spot account |
| utaMaxWithdrawal | String | Max withdrawable amount for unified trading account |
| totalMaxWithdrawal | String | Total max withdrawable amount |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Max-Withdrawal)


---

# Get Financial Records

### Description

Query financial records within the last 90 days.

### HTTP Request

- GET /api/v3/account/financial-records
- Rate limit: 20/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/financial-records?category=USDT-FUTURES&coin=BGB" \
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
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures <br> `OTHER` Other |
| coin | String | No | Coin name<br>e.g.,`BTC` |
| type | String | No | Type<br>`TRANSFER_IN`/`TRANSFER_OUT`......<br>All enumeration values can be viewed under the Enumeration category. |
| startTime | String | No | Start timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383085`<br>The access window is 90 days |
| endTime | String | No | End timestamp <br>A Unix timestamp in milliseconds e.g.,`1597026383185`<br> The time range between `startTime` and `endTime` must not exceed 30 days |
| limit | String | No | Limit per page<br>Default:`100`. Maximum:`100` |
| cursor | String | No | Cursor <br>Pagination is implemented by omitting the cursor in the first query and applying the cursor from the previous query for subsequent pages |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1750135478641,
  "data": {
    "list": [
      {
        "category": "Margin",
        "id": "13111111111111111",
        "symbol": "BTCUSDT",
        "coin": "BTC",
        "type": "ORDER_DEALT_IN",
        "amount": "0.00531168",
        "fee": "-0.00000531",
        "balance": "55.10017801",
        "ts": "1745853486185"
      }
    ],
    "cursor": "122222222222222222"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | List |
| >category | String | Product Type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures <br> `OTHER` Other |
| >id | String | Record ID |
| >symbol | String | Symbol name |
| >coin | String | Coin name |
| >type | String | Type<br>`TRANSFER_IN`/`TRANSFER_OUT`/......<br>All enumeration values can be viewed under the Enumeration category. |
| >amount | String | Amount |
| >fee | String | Fee |
| >balance | String | Balance |
| >ts | String | The timestamp that system generated the data<br>A Unix millisecond timestamp. |
| cursor | String | Cursor |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Financial-Records)


---

# Switch Account

### Description

1. Only supports parent accounts.
2. This endpoint is only used for switching to classic account mode.
3. Please note that since the account switching process takes approximately 1 minute, the successful response you receive only indicates that the request has been received, and does not mean that the account has been successfully switched to the classic account.
4. Please use the query switching status interface to confirm whether the account switching is successful.

### HTTP Request

- POST /api/v3/account/switch
- Rate limit: 1/sec/UID
Request

```
curl -X POST "https://api.bitget.com/api/v3/account/switch" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" \
   -d '{}'
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1728625799912,
  "data": null
}
```

### Response Parameters

N/A

[Source](https://www.bitget.com/api-doc/uta/account/Switch-Account)


---

# Get Switch Status

### Description

Only supports parent accounts.

### HTTP Request

- GET /api/v3/account/switch-status
- Rate limit: 5/sec/UID
Request

```
curl "https://api.bitget.com/api/v3/account/switch-status" \
   -H "ACCESS-KEY:*******" \
   -H "ACCESS-SIGN:*" \
   -H "ACCESS-PASSPHRASE:*" \
   -H "ACCESS-TIMESTAMP:1659076670000" \
   -H "locale:en-US" \
   -H "Content-Type: application/json" 
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1746687063471,
  "data": {
    "status": "fail",
    "reason": "upgrade_disabled"
  }
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| status | String | Switch Status<br> `process`Processing<br>`success`Success<br>`fail`Failed |
| reason | String | Failure Reason<br> Only returned when the `status = fail` |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Switch-Status)


---

# Get OI Limit

### Description

get open interest limit.

### HTTP Request

- GET /api/v3/account/open-interest-limit
- Rate limit: 5/sec/UID
Request

```
curl "https://api.bitget.com/api/v3/account/open-interest-limit?category=USDT-FUTURES&symbol=BTCUSDT" \
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
| symbol | String | Yes | Symbol <br>e.g.,`BTCUSDT` |
| category | String | Yes | Product type <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1764170997805,
  "data": {
    "symbol": "BTCUSDT",
    "singleUserLimit": "2766.30748",
    "masterSubLimit": "9173.8782",
    "marketMakerLimit": "0"
  }
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| symbol | String | Symbol name |
| singleUserLimit | String | Single User Limit Quantity. |
| masterSubLimit | String | Main/Sub-account Quantity Limit |
| marketMakerLimit | String | Market Maker Total Quota |

[Source](https://www.bitget.com/api-doc/uta/account/Get-OI-Limit)


---

# Get Account Funding Assets

### Description

Obtain fund account information and only return the coins with assets.

Note: For Pre-IPO tokens, the `coin` field uses mixed case, e.g., `preSPAX`. Please be mindful of case sensitivity. The exact `coin` value is subject to what is returned by the Get Coin Info API.

### HTTP Request

- GET /api/v3/account/funding-assets
- Rate limit: 20/sec/UID
- Permission: UTA mgt. (read)
Request

```
curl "https://api.bitget.com/api/v3/account/funding-assets" \
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
| coin | String | No | Coin name |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1750396239013,
  "data": [
    {
      "coin": "BGB",
      "available": "0.01",
      "frozen": "0",
      "balance": "0.01"
    },
    {
      "coin": "USDT",
      "available": "0.04",
      "frozen": "0",
      "balance": "0.04"
    }
  ]
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| coin | String | Coin name |
| balance | String | Balance <br>Unit: the current asset coin |
| available | String | Available <br>Unit: the current asset coin |
| frozen | String | Frozen <br>Unit: the current asset coin |

[Source](https://www.bitget.com/api-doc/uta/account/Get-Account-Funding-Assets)

