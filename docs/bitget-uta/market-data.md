# Bitget UTA API — Market Data (public REST)

| Endpoint / Channel | Slug |
| --- | --- |
| [Instruments](#instruments) | `public/Instruments` |
| [Tickers](#tickers) | `public/Tickers` |
| [OrderBook](#orderbook) | `public/OrderBook` |
| [Get Candle Data](#get-candle-data) | `public/Get-Candle-Data` |
| [Get History Candle Data](#get-history-candle-data) | `public/Get-History-Candle-Data` |
| [Fills](#fills) | `public/Fills` |
| [Get Current Funding Rate](#get-current-funding-rate) | `public/Get-Current-Funding-Rate` |
| [Get History Funding Rate](#get-history-funding-rate) | `public/Get-History-Funding-Rate` |
| [Get Open Interest](#get-open-interest) | `public/Get-Open-Interest` |
| [Get Contracts Oi](#get-contracts-oi) | `public/Get-Contracts-Oi` |
| [Get Position Tier Data](#get-position-tier-data) | `public/Get-Position-Tier-Data` |
| [Get Discount Rate](#get-discount-rate) | `public/Get-Discount-Rate` |
| [Get Index Components](#get-index-components) | `public/Get-Index-Components` |


---

# Get Instruments

### Description

Query the specifications for online trading pair instruments.

### HTTP Request

- GET /api/v3/market/instruments
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/instruments?category=SPOT"  \
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |

- Spot
- Futures
- Margin
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1770531248742,
  "data": [
    {
      "symbol": "BTCUSDT",
      "category": "SPOT",
      "baseCoin": "BTC",
      "quoteCoin": "USDT",
      "buyLimitPriceRatio": "0.02",
      "sellLimitPriceRatio": "0.02",
      "minOrderQty": "0.000001",
      "maxOrderQty": "0",
      "pricePrecision": "2",
      "quantityPrecision": "6",
      "quotePrecision": "8",
      "minOrderAmount": "1",
      "maxSymbolOrderNum": "",
      "maxProductOrderNum": "400",
      "status": "online",
      "maintainTime": "",
      "maxPositionNum": "200",
      "symbolType": "crypto",
      "launchTime": "1532454360000"
    }
  ]
}
```
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1770531054230,
  "data": [
    {
      "symbol": "BTCUSDT",
      "category": "USDT-FUTURES",
      "baseCoin": "BTC",
      "quoteCoin": "USDT",
      "isRwa": "NO",
      "isReality": "no",
      "buyLimitPriceRatio": "0.05",
      "sellLimitPriceRatio": "0.05",
      "feeRateUpRatio": "0.005",
      "makerFeeRate": "0.0002",
      "takerFeeRate": "0.0006",
      "openCostUpRatio": "0.01",
      "minOrderQty": "0.0001",
      "maxOrderQty": "1200",
      "pricePrecision": "1",
      "quantityPrecision": "4",
      "quotePrecision": "",
      "priceMultiplier": "0.1",
      "quantityMultiplier": "0.0001",
      "type": "perpetual",
      "minOrderAmount": "5",
      "maxSymbolOrderNum": "",
      "maxProductOrderNum": "400",
      "maxPositionNum": "200",
      "status": "online",
      "offTime": "-1",
      "limitOpenTime": "-1",
      "deliveryTime": "",
      "deliveryStartTime": "",
      "deliveryPeriod": "",
      "launchTime": "0",
      "fundInterval": "8",
      "minLeverage": "1",
      "maxLeverage": "150",
      "maintainTime": "",
      "symbolType": "crypto",
      "maxMarketOrderQty": "220"
    }
  ]
}
```
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1770531089306,
  "data": [
    {
      "symbol": "ADAUSDT",
      "category": "MARGIN",
      "baseCoin": "ADA",
      "quoteCoin": "USDT",
      "buyLimitPriceRatio": "0.02",
      "sellLimitPriceRatio": "0.02",
      "minOrderQty": "0.001",
      "maxOrderQty": "0",
      "pricePrecision": "4",
      "quantityPrecision": "3",
      "quotePrecision": "7",
      "minOrderAmount": "1",
      "maxSymbolOrderNum": "",
      "maxProductOrderNum": "400",
      "maxPositionNum": "200",
      "status": "online",
      "maintainTime": "",
      "isIsolatedBaseBorrowable": "YES",
      "isIsolatedQuotedBorrowable": "YES",
      "warningRiskRatio": "0.8",
      "liquidationRiskRatio": "1",
      "maxCrossedLeverage": "5",
      "maxIsolatedLeverage": "10",
      "userMinBorrow": "0.00000001",
      "areaSymbol": "no",
      "maxLeverage": "10",
      "symbolType": "crypto",
      "launchTime": null
    }
  ]
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| category | String | Product type <br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES`: USDT futures <br> `COIN-FUTURES`: Coin-M futures <br> `USDC-FUTURES`: USDC futures |
| symbol | String | Symbol name |
| isRwa | String | Is this an RWA Symbol<br> `YES` <br> `NO` |
| isReality | String | Reality identifier<br>`yes` Reality stock token<br>`no` Non-Reality stock token |
| baseCoin | String | Base coin <br>e.g.,`BTC`in`BTCUSDT` |
| quoteCoin | String | Quote coin <br>e.g.,`USDT`in`BTCUSDT` |
| buyLimitPriceRatio | String | Buy price limit ratio <br> The ratio of the buy limit price to the market price, determining the maximum price at which a buy order will be placed |
| sellLimitPriceRatio | String | Sell price limit ratio <br> The ratio of the sell limit price to the market price, determining the minimum price at which a sell order will be placed |
| feeRateUpRatio | String | Fee markup ratio <br>The percentage by which the actual fee is increased relative to the base fee |
| openCostUpRatio | String | Opening cost markup ratio <br> The percentage by which the cost of opening a trading position is increased relative to the base or standard cost |
| minOrderQty | String | Minimum order quantity <br>This refers to the smallest allowable quantity for placing an order in terms of the base coin <br> Only applicable to futures trading, for spot/margin, please refer to Trading Rules. |
| maxOrderQty | String | Maximum order quantity for a single limit order <br>This refers to the largest allowable quantity for placing an order in terms of the base coin <br> Only applicable to futures trading, for spot/margin, please refer to Trading Rules. <br>A value of 0 indicates no limit. |
| minOrderAmount | String | Minimum order amount <br> This refers to the smallest allowable amount for placing an order in terms of the quote coin |
| pricePrecision | String | Price precision <br> The number of decimal places allowed for the price |
| quantityPrecision | String | Quantity precision <br> The number of decimal places allowed for the quantity |
| quotePrecision | String | Market order precision <br> The number of decimal places allowed for the price of the quote coin |
| priceMultiplier | String | Price multiplier <br> Used for futures orders, along with `pricePrecision` <br> Example: `pricePrecision`: 2 & `priceMultiplier`: 0.02<br>The order price must be a multiple of `priceMultiplier` and have two decimal places (e.g.,0.08, 1.14, 2.36) |
| quantityMultiplier | String | Quantity multiplier <br> Used for futures orders, along with `quantityPrecision` <br> Example: `quantityPrecision`: 2 & `quantityMultiplier`: 0.02<br>The order quantity must be a multiple of `quantityMultiplier` and have two decimal places (e.g.,0.08, 1.14, 2.36) |
| type | String | Futures type <br> `perpetual` Perpetual <br> `delivery` Delivery |
| maxSymbolOrderNum | String | Maximum order number in terms of the trading pair |
| maxProductOrderNum | String | Maximum order number in terms of the product line |
| maxPositionNum | String | Maximum position number in terms of the trading pair |
| status | String | Trading pair status <br> `listed` Listed (not yet open) <br> `online` Normal <br> `limit_open` Restrict opening positions <br> `limit_close` Restrict closing positions <br> `offline` Delisted/under maintenance <br> `restrictedAPI` API restricted |
| offTime | String | Trading halt time. <br> If not configured, it returns: "" |
| limitOpenTime | String | Restricted open time <br> If not configured, it returns: ""; Other values indicate symbol is under/expected maintenance and trading is prohibited after a specified time |
| deliveryTime | String | Delivery time <br> Available only for deliveries |
| deliveryStartTime | String | Delivery start time <br> Available only for deliveries |
| deliveryPeriod | String | Delivery period <br> `this_quarter` This quarter <br> `next_quarter` Next quarter <br> Available only for deliveries |
| launchTime | String | Launch time <br> Unix millisecond timestamp indicating when the trading pair was launched |
| fundInterval | String | Funding Interval <br> `1` Every 1 hour <br>`8` Every 8 hours |
| minLeverage | String | Minimum leverage |
| maxLeverage | String | Maximum leverage |
| maintainTime | String | Maintenance time <br> If not configured, it returns: "" |
| isIsolatedBaseBorrowable | String | Base coin borrowable status <br> Available only for margin trading |
| isIsolatedQuotedBorrowable | String | Quote coin borrowable status <br> Available only for margin trading |
| warningRiskRatio | String | Warning risk ratio |
| liquidationRiskRatio | String | Liquidation risk ratio |
| maxCrossedLeverage | String | Maximum leverage for cross margin <br> Available only for margin trading |
| maxIsolatedLeverage | String | Maximum leverage for isolated margin <br> Available only for margin trading |
| userMinBorrow | String | Minimum borrowable amount <br> Available only for margin trading |
| areaSymbol | String | Area symbol<br> `YES`/`NO` <br> Available only for Spot trading <br> Only return this parameter for pairs where the value is `YES`. |
| makerFeeRate | String | Maker fee rate<br>In decimal form, e.g., 0.0002 represents 0.02% |
| takerFeeRate | String | Taker fee rate<br>In decimal form, e.g., 0.0002 represents 0.02% |
| maxMarketOrderQty | String | Maximum order quantity for a single market order <br>This refers to the largest allowable quantity for placing an order in terms of the base coin |
| symbolType | String | Symbol Types <br> `crypto` cryptocurrency <br>`metal` precious metals <br>`stock` stocks <br>`commodity` commodities |

[Source](https://www.bitget.com/api-doc/uta/public/Instruments)


---

# Get Tickers

### Description

Query real-time market data, including the latest price, 24-hour high/low, volume, bid, ask, and price change for available trading pairs.

### HTTP Request

- GET /api/v3/market/tickers
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/tickers?category=SPOT&symbol=BTCUSDT"  \
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| category | String | Yes | Product type <br> `SPOT` Spot trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |

- Spot
- Futures
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1765444397411,
  "data": [
    {
      "category": "SPOT",
      "symbol": "BTCUSDT",
      "ts": "1765444395778",
      "lastPrice": "90253.5",
      "openPrice24h": "92590.86",
      "highPrice24h": "94475.75",
      "lowPrice24h": "89394.71",
      "ask1Price": "90253.5",
      "bid1Price": "90253.49",
      "bid1Size": "2.368684",
      "ask1Size": "0.402938",
      "price24hPcnt": "-0.02524",
      "volume24h": "7386.014738",
      "turnover24h": "677732572.225658"
    }
  ]
}
```
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1765444282767,
  "data": [
    {
      "category": "USDT-FUTURES",
      "symbol": "BTCUSDT",
      "ts": "1765444282230",
      "lastPrice": "90216.3",
      "openPrice24h": "92629",
      "highPrice24h": "94477.8",
      "lowPrice24h": "89350.1",
      "ask1Price": "90216.4",
      "bid1Price": "90216.3",
      "bid1Size": "11.0845",
      "ask1Size": "4.2309",
      "price24hPcnt": "-0.02605",
      "volume24h": "50772.839",
      "turnover24h": "4667820987.88478",
      "indexPrice": "90247.5410266912815894",
      "markPrice": "90216.4",
      "fundingRate": "0.000047",
      "openInterest": "27606.0718",
      "deliveryStartTime": "",
      "deliveryTime": "",
      "deliveryStatus": ""
    }
  ]
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| symbol | String | Symbol name <br>e.g.,`BTCUSDT` |
| category | String | Product type <br> `SPOT` Spot trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| lastPrice | String | Latest price |
| openPrice24h | String | Market price 24 hours ago |
| lowPrice24h | String | Lowest price in the last 24 hours |
| highPrice24h | String | Highest price in the last 24 hours |
| ask1Price | String | Best ask price |
| bid1Price | String | Best bid price |
| bid1Size | String | Best bid quantity |
| ask1Size | String | Best ask quantity |
| price24hPcnt | String | 24-hour price change percentage |
| turnover24h | String | 24-hour turnover |
| volume24h | String | 24-hour volume |
| indexPrice | String | Index price <br> Available only for futures |
| markPrice | String | Mark price <br> Available only for futures |
| fundingRate | String | Funding rate <br> Available only for futures |
| openInterest | String | Open interest <br> Available only for futures |
| deliveryStartTime | String | Delivery start time <br> Available only for deliveries |
| deliveryTime | String | Delivery time <br> Available only for deliveries |
| deliveryStatus | String | Delivery status <br> `delivery_config_period` New pair configuration <br> `delivery_normal` Trading <br> `delivery_before` 10 minutes before delivery, no new orders <br> `delivery_period` During delivery, no opening or closing of positions, and no order cancellation <br> Available only for deliveries |
| ts | String | The timestamp that the system generated the data <br>A Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/public/Tickers)


---

# Get OrderBook

### Description

Query order book depth data.

### HTTP Request

- GET /api/v3/market/orderbook
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/orderbook?category=USDT-FUTURES&symbol=BTCUSDT&limit=5"  \
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br>`SPOT` Spot trading<br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| limit | String | No | Level <br>Default: `5`. Maximum: `200` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730969017897,
  "data": {
    "a": [
      [
        73000.0,
        0.007
      ],
      [
        74000.0,
        0.007
      ],
      [
        75000.0,
        0.007
      ],
      [
        75123.0,
        5.615
      ]
    ],
    "b": [
      [
        71213.8,
        1.836
      ],
      [
        71213.3,
        10.000
      ],
      [
        71212.8,
        10.000
      ]
    ],
    "ts": "1730969017964" // Match engine timestamp
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| a | Array | Sell Asks. Sort by price in ascending order |
| > Index 0 | Number | Sell price |
| > Index 1 | Number | Sell quantity |
| b | Array | Buy bids. Sort by price in descending order |
| > Index 0 | Number | Buy Price |
| > Index 1 | Number | Buy quantity |
| ts | String | The timestamp that the system generated data <br>A Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/public/OrderBook)


---

# Get Kline/Candlestick

### Description

Query kline/candlestick data. This endpoint allows retrieving up to 1,000 candlesticks.

### HTTP Request

- GET /api/v3/market/candles
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/candles?category=USDT-FUTURES&symbol=BTCUSDT&interval=1m&type=MARKET&limit=10"
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br>`SPOT` Spot trading<br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| interval | String | Yes | Granularity<br>`1m`,`3m`,`5m`,`15m`,`30m`,`1H`,`4H`,`6H`,`12H`,`1D` |
| startTime | String | No | Start timestamp <br>A Unix millisecond timestamp, e.g.,`1672410780000` |
| endTime | String | No | End timestamp <br>A Unix millisecond timestamp, e.g.,`1672410781000` |
| type | String | No | Candlestick type<br> `market`, `mark`, `index`, `premium`. Default: `market` |
| limit | String | No | Limit per page <br> Default:`1000`. Maximum: `100` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695865864944,
  "data": [
    [
      "1687708800000",
      "27176.93",
      "27177.43",
      "27166.93",
      "27177.43",
      "2990.08",
      "81246917.3294"
    ],
    [
      "1688313600000",
      "27177.43",
      "27177.43",
      "24000",
      "24001",
      "2989.1",
      "72450031.0448"
    ]
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| index[0] | String | The timestamp that the system generated the data <br>A Unix timestamp in milliseconds |
| index[1] | String | Open price |
| index[2] | String | Highest price |
| index[3] | String | Lowest price |
| index[4] | String | Close price |
| index[5] | String | Trade Volume<br>*The unit is base coin* |
| index[6] | String | Turnover<br>*The unit is quote coin* |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Candle-Data)


---

# Get Kline/Candlestick History

### Description

You can retrieve historical candlestick data from more than 90 days ago.

Note: If endTime goes past a candle interval boundary (even by 1 ms), the system may round up when calculating the number of candles, and the response may include one additional interval (i.e., the returned data may start one interval earlier).

### HTTP Request

- GET /api/v3/market/history-candles
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/history-candles?category=USDT-FUTURES&symbol=BTCUSDT&interval=1D&limit=10"
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br>`SPOT` Spot trading<br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| interval | String | Yes | Granularity<br>`1m`,`3m`,`5m`,`15m`,`30m`,`1H`,`4H`,`6H`,`12H`,`1D` |
| startTime | String | No | Start timestamp <br>A Unix millisecond timestamp, e.g.,`1672410780000`<br>Request data after this start time (the maximum time query range is 90 days) |
| endTime | String | No | End timestamp <br>A Unix millisecond timestamp, e.g.,`1672410781000`<br>Request data before this end time (the maximum time query range is 90 days) |
| type | String | No | Candlestick type<br> `market`, `mark`, `index`, `premium`. Default: `market` |
| limit | String | No | Limit per page <br> Default:`100`. Maximum: `100` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1695865864944,
  "data": [
    [
      "1687708800000",
      "27176.93",
      "27177.43",
      "27166.93",
      "27177.43",
      "2990.08",
      "81246917.3294"
    ],
    [
      "1688313600000",
      "27177.43",
      "27177.43",
      "24000",
      "24001",
      "2989.1",
      "72450031.0448"
    ]
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| >index[0] | String | The timestamp that the system generated the data |
| >index[1] | String | Open price |
| >index[2] | String | Highest price |
| >index[3] | String | Lowest price |
| >index[4] | String | Close price |
| >index[5] | String | Volume. *The unit is base coin* |
| >index[6] | String | Turnover. *The unit is quote coin* |

[Source](https://www.bitget.com/api-doc/uta/public/Get-History-Candle-Data)


---

# Get Recent Public Fills

### Description

Query recent public fill data on Bitget.

### HTTP Request

- GET /api/v3/market/fills
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/fills?category=USDT-FUTURES&symbol=BTCUSDT&limit=100"  \
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br> `SPOT` Spot trading <br> `MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| limit | String | No | Limit per page <br>Default: `100`. Maximum: `100` |

Response

```
{
  "code": "00000",
  "data": [
    {
      "execId": "1",
      "execLinkId": "12345877111",
      "price": "29990.5",
      "size": "0.0166",
      "side": "sell",
      "ts": "1627116776464",
      "isRPI": "no"
    },
    {
      "execId": "2",
      "execLinkId": "12345877112",
      "price": "30007.0",
      "size": "0.0166",
      "side": "buy",
      "ts": "1627116600875",
      "isRPI": "yes"
    }
  ],
  "msg": "success",
  "requestTime": 1690313813709
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| >execId | String | Fill execution ID |
| >execLinkId | String | Execution correlation ID |
| >price | String | Fill price |
| >size | String | Fill size <br>`COIN-Futures`：The unit is **quote coin** <br>`Others`：The unit is **base coin**<br> |
| >side | String | Trade side <br> `sell`/`buy` |
| >ts | String | Fill timestamp <br> A Unix timestamp in milliseconds |
| >isRPI | String | Whether it is an RPI fill<br>`yes` Yes<br>`no` No |

[Source](https://www.bitget.com/api-doc/uta/public/Fills)


---

# Get Current Funding Rate

### Description

Get current funding rate.

### HTTP Request

- GET /api/v3/market/current-fund-rate
- Rate limit: 20次/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/current-fund-rate?symbol=BTCUSDT" \
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| category | String | No | Product type<br>`USDT-FUTURES` USDT futures<br>`COIN-FUTURES` Coin-M futures<br>`USDC-FUTURES` USDC futures |
| symbol | String | No | Trading pair, based on the symbolName, i.e. BTCUSDT |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1743059269376,
  "data": [
    {
      "symbol": "BTCUSDT",
      "fundingRate": "0.000071",
      "fundingRateInterval": "8",
      "nextUpdate": "1743062400000",
      "minFundingRate": "-0.003",
      "maxFundingRate": "0.003",
      "cashDividend": "0",
      "cashDividendNextUpdate": "0"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| >symbol | String | Trading pair name |
| >fundingRate | String | Current funding rates |
| >fundingRateInterval | String | Funding rate settlement period<br>Unit: hour. Enumeration values include 1, 2, 4, 8. 1 represents 1 hour, 2 represents 2 hours, and so on. |
| >nextUpdate | String | Next update time<br>Unix timestamp in milliseconds |
| >minFundingRate | String | Lower limit of funding rate <br>Returned in decimal form. 0.025 represents 2.5%. |
| >maxFundingRate | String | Upper limit of funding rate <br>Returned in decimal form. 0.025 represents 2.5%. |
| >cashDividend | String | Cash dividend<br>Unit: USDT |
| >cashDividendNextUpdate | String | Next update time for cash dividend<br>Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Current-Funding-Rate)


---

# Get Funding Rate History

### Description

Query historical funding rate records. The Funding interval varies by symbol and can be retrieved via the Instruments endpoint.

### HTTP Request

- GET /api/v3/market/history-fund-rate
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/history-fund-rate?category=USDT-FUTURES&symbol=BTCUSDT&limit=10&cursor=1" \
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | Yes | Symbol name <br>e.g.,`BTCUSDT` |
| cursor | String | No | Page number <br>Default: `1`. Maximum: `100` |
| limit | String | No | Limit per page<br> Default: `20`. Maximum: `100` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1754908278922,
  "data": {
    "resultList": [
      {
        "symbol": "BTCUSDT",
        "fundingRate": "0.0001",
        "fundingRateTimestamp": "1754899200000"
      },
      {
        "symbol": "BTCUSDT",
        "fundingRate": "0.0001",
        "fundingRateTimestamp": "1754870400000"
      }
    ]
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| resultList | List<Object> | Data list |
| > symbol | String | Symbol name |
| > fundingRate | String | Funding rate |
| > fundingRateTimestamp | String | Funding rate timestamp<br> A Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate)


---

# Get Open Interest

### Description

Query the total number of unsettled or open futures

### HTTP Request

- GET /api/v3/market/open-interest
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/open-interest?category=USDT-FUTURES&symbol=BTCUSDT"
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product Type<br>`USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730969652387,
  "data": {
    "list": [
      {
        "symbol": "BTCUSDT",
        "openInterest": "2243.019"
      }
    ],
    "ts": "1730969652411"
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| list | Array | Data list |
| >symbol | String | Symbol name |
| >openInterest | String | Open interest |
| ts | String | The timestamp that the system generated the data <br>A Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Open-Interest)


---

# Get Open Interest Limit

### Description

Interface is used to get future contract OI Limit.

### HTTP Request

- GET /api/v3/market/oi-limit
- Rate Limit: 10 req/sec/IP
Request Example

```
curl "https://api.bitget.com/api/v3/market/oi-limit?category=usdt-futures"
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| symbol | String | No | Trading pair, based on the symbolName, i.e. BTCUSDT |
| category | String | Yes | Product type<br>`USDT-FUTURES` USDT-M Futures<br>`COIN-FUTURES` Coin-M Futures<br>`USDC-FUTURES` USDC-M Futures<br> |

Response Example

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1741665165571,
  "data": [{
    "symbol": "BTCUSDT",
    "notionalValue": "1000000",
    "totalNotionalValue": "5000000"
  },
    {
      "symbol": "ETHUSDT",
      "notionalValue": "1000000",
      "totalNotionalValue": "5000000"
    }]
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| > symbol | String | Product name |
| > notionalValue | String | Individual User Position Notional Value |
| > totalNotionalValue | String | Sub-account and Main-account Position Notional Value |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Contracts-Oi)


---

# Get Position Tier

### Description

Query the position tier info.

### HTTP Request

- GET /api/v3/market/position-tier
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/position-tier?category=USDT-FUTURES&symbol=BTCUSDT"  \
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| category | String | Yes | Product type<br>`MARGIN` Margin trading <br> `USDT-FUTURES` USDT futures <br> `COIN-FUTURES` Coin-M futures <br> `USDC-FUTURES` USDC futures |
| symbol | String | No | Symbol name <br>e.g.,`BTCUSDT`, applies to `Futures` |
| coin | String | No | Coin name <br>e.g.,`BTC`, applies to `Margin` |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1731146663643,
  "data": [
    {
      "tier": "1",
      "minTierValue": "0",
      "maxTierValue": "100000",
      "leverage": "125",
      "mmr": "0.004"
    }
  ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| tier | String | Position tier |
| leverage | String | Leverage |
| minTierValue | String | Minimum value of current tier<br> Available only for futures |
| maxTierValue | String | Maximum value of current tier<br> Available only for futures |
| mmr | String | Maintenance margin ratio<br> When the margin rate of the position falls below the maintenance margin rate, forced liquidation or position reduction will be triggered |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Position-Tier-Data)


---

# Get Discount Rate

### Description

Query discount rate applied to margin loans.

### HTTP Request

- GET /api/v3/market/discount-rate
- Rate limit: 20/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/discount-rate" \
```

### Request Parameters

N/A
Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1730911874605,
  "data": [
      {
        "coin": "ETH",
        "list": [
          {
            "tierStartValue": "0",
            "discountRate": "0.99"
          },
          {
            "tierStartValue": "40000",
            "discountRate": "0.98"
          }
        ]
      }
    ]
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| coin | String | Coin name<br>e.g.,`BTC` |
| list | Array | List |
| >tierStartValue | String | Tier start value <br> e.g., [0,10000 U）: `tierStartValue=0`<br>[10000 U,20000 U）: `tierStartValue=10000`<br>[20000 U,∞）: `tierStartValue=20000` |
| >discountRate | String | Discount rate |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Discount-Rate)


---

# Get Index Price Components

### Description

Get index price components

### HTTP Request

- GET /api/v3/market/index-components
- Rate limit: 10/sec/IP
Request

```
curl "https://api.bitget.com/api/v3/market/index-components?symbol=BTCUSDT"
```

### Request Parameters

| Parameter | Type | Required | Comments |
| --- | --- | --- | --- |
| symbol | String | YES | Trading pair, e.g. BTCUSDT |

Response

```
{
  "code": "00000",
  "msg": "success",
  "requestTime": 1767159256214,
  "data": {
    "symbol": "BTCUSDT",
    "componentList": [
      {
        "exchange": "BITGET_FUTURE",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88432.1",
        "weight": "0.4696"
      },
      {
        "exchange": "GATEIO",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88467",
        "weight": "0.164"
      },
      {
        "exchange": "BINANCE",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88456.65",
        "weight": "0.1434"
      },
      {
        "exchange": "MEXC",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88457.1",
        "weight": "0.0992"
      },
      {
        "exchange": "BITGET",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88469.77",
        "weight": "0.0768"
      },
      {
        "exchange": "OKX",
        "spotPair": "BTC/USDT",
        "equivalentPrice": "88463",
        "weight": "0.0468"
      }
    ]
  }
}
```

### Response Parameters

| Parameter | Type | Comments |
| --- | --- | --- |
| symbol | String | Trading pair |
| componentList | List<Object> | Index price component list |
| > exchange | String | Exchange |
| > spotPair | String | Spot trading pair name |
| > equivalentPrice | String | Equivalent price |
| > weight | String | Calculation weight <br>- Decimal format, e.g., 0.5 represents 50% |

[Source](https://www.bitget.com/api-doc/uta/public/Get-Index-Components)

