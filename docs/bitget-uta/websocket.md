# Bitget UTA API — WebSocket (public + private channels)

| Endpoint / Channel | Slug |
| --- | --- |
| [Tickers Channel](#tickers-channel) | `websocket/public/Tickers-Channel` |
| [Candlesticks Channel](#candlesticks-channel) | `websocket/public/Candlesticks-Channel` |
| [Order Book Channel](#order-book-channel) | `websocket/public/Order-Book-Channel` |
| [New Trades Channel](#new-trades-channel) | `websocket/public/New-Trades-Channel` |
| [Liquidation Channel](#liquidation-channel) | `websocket/public/Liquidation-Channel` |
| [Account Channel](#account-channel) | `websocket/private/Account-Channel` |
| [Positions Channel](#positions-channel) | `websocket/private/Positions-Channel` |
| [Order Channel](#order-channel) | `websocket/private/Order-Channel` |
| [Fill Channel](#fill-channel) | `websocket/private/Fill-Channel` |
| [Fast Fill Channel](#fast-fill-channel) | `websocket/private/Fast-Fill-Channel` |
| [Place Order Channel](#place-order-channel) | `websocket/private/Place-Order-Channel` |
| [Cancel Order Channel](#cancel-order-channel) | `websocket/private/Cancel-Order-Channel` |
| [Modify Order Channel](#modify-order-channel) | `websocket/private/Modify-Order-Channel` |
| [Batch Place Order Channel](#batch-place-order-channel) | `websocket/private/Batch-Place-Order-Channel` |
| [Batch Cancel Order Channel](#batch-cancel-order-channel) | `websocket/private/Batch-Cancel-Order-Channel` |
| [Batch Modify Order Channel](#batch-modify-order-channel) | `websocket/private/Batch-Modify-Order-Channel` |
| [Strategy Order Channel](#strategy-order-channel) | `websocket/private/Strategy-Order-Channel` |
| [ADL Notification Channel](#adl-notification-channel) | `websocket/private/ADL-Notification-Channel` |


---

# Tickers Channel

### Description

Get the latest transaction price, best bid, best ask, and 24-hour trading volume for the product. When there are changes (transactions, best bid/ask updates), the push frequency is:

- Spot push frequency: 200-300ms
- Futures push frequency: 300-400ms
Request

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "spot",
            "topic": "ticker",
            "symbol": "BTCUSDT"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe |
| args | List<Object> | Yes | Subscribed channel |
| > instType | String | Yes | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Yes | Topic <br> `ticker` Market data channel |
| > symbol | String | Yes | Trading pair <br> e.g. `BTCUSDT` |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "spot",
    "topic": "ticker",
    "symbol": "BTCUSDT"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `ticker` Ticker channel |
| > symbol | String | Trading pair <br> e.g. `BTCUSDT` |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "bid1Price": "99999",
      "lowPrice24h": "98200",
      "ask1Size": "188.312553",
      "volume24h": "37.722858",
      "price24hPcnt": "0.01833",
      "highPrice24h": "100000",
      "turnover24h": "3750302.979626",
      "bid1Size": "186.183209",
      "ask1Price": "100000",
      "openPrice24h": "0",
      "lastPrice": "100000"
    }
  ],
  "arg": {
    "instType": "spot",
    "symbol": "BTCUSDT",
    "topic": "ticker"
  },
  "action": "snapshot",
  "ts": 1736371332162
}
```

### Push Parameters

#### Spot Push

| Parameters | Parameter Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`spot` Spot trading |
| > topic | String | Topic <br> `ticker` Ticker channel |
| > symbol | String | Symbol name |
| action | String | Data push action <br> `snapshot` Full data |
| data | List<Object> | Subscribed data |
| > highPrice24h | String | Highest price in the last 24 hours |
| > lowPrice24h | String | Lowest price in the last 24 hours |
| > openPrice24h | String | Market price 24 hours ago |
| > lastPrice | String | Latest price |
| > turnover24h | String | 24-hour turnover |
| > volume24h | String | 24-hour volume |
| > bid1Price | String | Best bid price |
| > ask1Price | String | Best ask price |
| > bid1Size | String | Best bid quantity |
| > ask1Size | String | Best ask quantity |
| > price24hPcnt | String | 24-hour price change percentage |

#### Futures Push

| Parameters | Parameter Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed successful channel |
| > instType | String | Product type<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `ticker` Ticker channel |
| action | String | Data push action <br> `snapshot` Full data |
| data | List<Object> | Subscribed data |
| > highPrice24h | String | Highest price in the last 24 hours |
| > lowPrice24h | String | Lowest price in the last 24 hours |
| > openPrice24h | String | Market price 24 hours ago |
| > lastPrice | String | Latest price |
| > turnover24h | String | 24-hour turnover |
| > volume24h | String | 24-hour volume |
| > bid1Price | String | Best bid price |
| > ask1Price | String | Best ask price |
| > bid1Size | String | Best bid quantity |
| > ask1Size | String | Best ask quantity |
| > price24hPcnt | String | 24-hour price change percentage |
| > indexPrice | String | Index price <br> Available only for futures |
| > markPrice | String | Mark price <br> Available only for futures |
| > fundingRate | String | Funding rate <br> Available only for futures |
| > nextFundingTime | String | Next funding rate settlement time |
| > openInterest | String | Open interest <br> Available only for futures |
| > deliveryTime | String | Delivery time <br> Available only for deliveries |
| > deliveryStartTime | String | Delivery start time <br> Available only for deliveries |
| > deliveryStatus | String | Delivery status <br> `delivery_config_period` New pair configuration <br> `delivery_normal` Trading <br> `delivery_before` 10 minutes before delivery, no new orders <br> `delivery_period` During delivery, no opening or closing of positions, and no order cancellation <br> Available only for deliveries |

[Source](https://www.bitget.com/api-doc/uta/websocket/public/Tickers-Channel)


---

# Candlestick Channel

### Description

Get the candlestick data of the product

- When there are transactions in the K-line channel, data is pushed once per second.
- When there are no transactions, data is pushed once at the specified time granularity.
Request

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "spot",
            "topic": "kline",
            "symbol": "BTCUSDT",
            "interval": "1D"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Action <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe |
| args | List<Object> | Yes | Subscribed channel |
| > instType | String | Yes | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Yes | Topic <br> `kline` K-line channel |
| > symbol | String | Yes | Symbol name <br> e.g., `BTCUSDT` |
| > interval | String | Yes | Interval <br> `1m`, `3m`,`5m`, `15m`, `30m`, `1H`, `4H`,`6H`, `12H`, `1D` |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "spot",
    "topic": "kline",
    "symbol": "BTCUSDT",
    "interval": "1D"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Action <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe <br> `error` Parameter error |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `kline` K-line channel |
| > symbol | String | Symbol name <br> e.g., `BTCUSDT` |
| > interval | String | Interval |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "volume": "0.423",
      "high": "400005",
      "low": "276670",
      "start": "1710518400000",
      "close": "400005",
      "turnover": "148190.38375",
      "open": "276670"
    }
  ],
  "arg": {
    "instType": "spot",
    "symbol": "BTCUSDT",
    "topic": "kline",
    "interval": "1D"
  },
  "action": "snapshot",
  "ts": 1736370735556
}
```

### Push Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `kline` |
| > symbol | String | Symbol name |
| action | String | Push data action <br> `snapshot` Full data <br> `update` Incremental data |
| data | List<Object> | Subscribed data |
| > start | String | The timestamp that the system generated the data <br>A Unix timestamp in milliseconds |
| > open | String | Open price |
| > close | String | Close price |
| > high | String | Highest price |
| > low | String | Lowest price |
| > volume | String | Trade Volume<br>*The unit is base coin* |
| > turnover | String | Turnover<br>*The unit is quote coin* |

[Source](https://www.bitget.com/api-doc/uta/websocket/public/Candlesticks-Channel)


---

# Depth Channel

### Description

Push depth data. `books` is the full depth channel, `books1` is the 1-level channel, `books5` is the 5-level channel, and `books50` is the 50-level channel:

- `books` corresponds to full-depth data, the first push is the full data `snapshot`, subsequent pushes are incremental updates: `update`
- `books1` corresponds to 1-level depth data, every push: `snapshot`
- `books5` corresponds to 5-level depth data, every push: `snapshot`
- `books50` corresponds to 50-level depth data, every push: `snapshot`

Spot

- `books1` push frequency: 1ms
- `books5` push frequency: 10ms
- `books50` push frequency: 20ms
- `books` push frequency: 50ms

Futures

- `books1` push frequency: 1ms
- `books5` push frequency: 10ms
- `books50` push frequency: 20ms
- `books` push frequency: 50ms

The serial number of the previous push `pseq` :

- Under normal circumstances, the serial numbers pushed by the depth channel are incremental, meaning that the `seq` value received in a push sequence is always greater than the `pseq`.
- In cases such as system releases or service restarts, the serial numbers may be reset. At this time, users will most likely receive a push message with `pseq=0`. After the reset, all subsequent messages will continue to be ordered normally.
- The seq of the previous update incremental message must be equal to the pseq of the following update incremental message.
- The seq of update incremental messages should be increasing, except during symbol maintenance.
- After receiving a snapshot (full snapshot), the seq of the snapshot should fall within the range [pseq, seq] of the first update incremental data received after the snapshot.
Request

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "usdt-futures",
            "topic": "books",
            "symbol": "BTCUSDT"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe |
| args | List<Object> | Yes | Subscribed channel |
| > instType | String | Yes | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Yes | Topic <br> `books` All levels <br> `books1` 1 level <br> `books5` 5 levels <br> `books50` 50 levels |
| > symbol | String | Yes | Symbol name <br> e.g., `BTCUSDT` |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "usdt-futures",
    "topic": "books1",
    "symbol": "BTCUSDT"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic |
| > symbol | String | Symbol name |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "a": [
        [
          "99756.7",
          "23.9774"
        ]
      ],
      "b": [
        [
          "99756.6",
          "0.0128"
        ]
      ],
      "pseq": 0,
      "seq": 1304314508780744705,
      "maxDepth": "50",
      "ts": "1746698732562"
    }
  ],
  "arg": {
    "instType": "usdt-futures",
    "symbol": "BTCUSDT",
    "topic": "books"
  },
  "action": "snapshot",
  "ts": 1746698732563
}
```

### Push Parameters

| Return Field | Parameter Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic |
| > symbol | String | Symbol name |
| action | String | Data push action <br> `snapshot` Full push <br> `update` Incremental push |
| ts | String | Data push timestamp |
| data | List<Object> | Subscribed data |
| > a | List<String> | Sell Asks. Sort by price in ascending order |
| > > a[0] | String | Sell price |
| > > a[1] | String | Sell quantity |
| > b | List<String> | Buy bids. Sort by price in descending order |
| > > b[0] | String | Buy Price |
| > > b[1] | String | Buy quantity |
| > > seq | String | Serial number.<br> It increments when the order book is updated and can be used to determine whether there is out-of-order packets. |
| > > pseq | String | The serial number of the previous push. <br> Can be used to determine if there has been packet loss. This field only has a value for the books channel. |
| > maxDepth | String | Maximum depth levels.<br>Range: [0,1000].<br>Positive integer, varies between trading pairs.<br>This field only has a value for the `books` channel. |
| > > ts | String | The timestamp that the system generated data <br>A Unix timestamp in milliseconds |

[Source](https://www.bitget.com/api-doc/uta/websocket/public/Order-Book-Channel)


---

# Public Trades Channel

### Description

To subscribe the public trades channel

Real-time Push
Request

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "usdt-futures",
            "topic": "publicTrade",
            "symbol": "BTCUSDT"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe |
| args | List<Object> | Yes | Subscribed channel |
| > instType | String | Yes | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Yes | Topic <br> `publicTrade` Public trade |
| > symbol | String | Yes | Symbol name <br> e.g. `BTCUSDT` |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "usdt-futures",
    "topic": "publicTrade",
    "symbol": "BTCUSDT"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe <br> `error` Parameter error |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `publicTrade` Public trade channel |
| > symbol | String | Symbol name |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "p": "100000",
      "S": "buy",
      "T": "1736348770627",
      "v": "0.00118",
      "i": "1260903622036942849",
      "L": "1234568787787878787",
      "isRPI": "no"
    }
  ],
  "arg": {
    "instType": "usdt-futures",
    "symbol": "BTCUSDT",
    "topic": "publicTrade"
  },
  "action": "snapshot",
  "ts": 1736371104297
}
```

### Push Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`spot` Spot trading<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `publicTrade` |
| > symbol | String | Symbol name |
| action | String | Push data action <br> `snapshot` Full data <br> `update` Incremental data |
| data | List<Object> | Subscribed data |
| > i | String | Fill execution ID |
| > L | String | Execution correlation ID |
| > p | String | Fill price |
| > v | String | Fill size <br>`COIN-Futures`：The unit is **quote coin** <br>`Others`：The unit is **base coin**<br> |
| > S | String | Fill side <br> `sell`/`buy` |
| > T | String | Fill timestamp <br> A Unix timestamp in milliseconds |
| > isRPI | String | Whether it is an RPI fill<br>`yes` Yes<br>`no` No |

[Source](https://www.bitget.com/api-doc/uta/websocket/public/New-Trades-Channel)


---

# Liquidation Channel

### Description

Liquidation Channel The push frequency is once every 20 seconds or every 10,000 records; that is, data is pushed once every 20 seconds or when 10,000 records are accumulated, and each push contains all forced-liquidation data on the platform.
Request

```
{
    "args": [
        {
            "instType": "usdt-futures",
            "topic": "liquidation"
        }
    ],
    "op": "subscribe"
}
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe |
| args | List<Object> | Yes | Subscribed channel |
| > instType | String | Yes | Product type<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Yes | Topic <br> `liquidation` Liquidation channel |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "usdt-futures",
    "topic": "liquidation"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `liquidation` Liquidation channel |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "symbol": "BTCUSDT",
      "side": "buy",
      "price": "89000",
      "amount": "37.722858",
      "ts": "1736371332162"
    }
  ],
  "arg": {
    "instType": "usdt-futures",
    "topic": "liquidation"
  },
  "action": "update",
  "ts": 1736371332162
}
```

### Push Parameters

| Parameters | Parameter Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed successful channel |
| > instType | String | Product type<br>`usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > topic | String | Topic <br> `liquidation` Liquidation channel |
| action | String | Data push action <br> `snapshot` Full data <br> `update` Incremental data |
| data | List<Object> | Subscribed data |
| >symbol | String | Symbol name |
| >side | String | Position side<br>`buy`Long position liquidation<br>`sell`Short position liquidation |
| >price | String | Liquidation price |
| >amount | String | Liquidation amount <br> Unit: quote coin. |
| >ts | String | Liquidation time (Unix millisecond timestamp) |

[Source](https://www.bitget.com/api-doc/uta/websocket/public/Liquidation-Channel)


---

# Account

### Description

Data will be pushed when the following events occurred:

1. Push on first-time subscription
2. Push when spot/margin/futures orders are filled in the unified trading account
3. Push when the fund settlement is done
4. Push when balance changes (transfers, airdrops, loans, etc.)
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "account"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe`/`unsubscribe` |
| args | List<Object> | Yes | Subscribed channels |
| > instType | String | Yes | Product Type <br> `UTA` Unified trading account |
| > topic | String | Yes | Topic<br>`account` |

Response

```
{
    "event": "subscribe",
    "arg": {
        "instType": "UTA",
        "topic": "account"
    },
    "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| event | String | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe <br> `error` Parameter Error |
| arg | Object | Subscribed Channel |
| > instType | String | Product Type <br> `UTA` |
| > topic | String | Topic <br> `account` |
| code | String | Error Code |
| msg | String | Error Message |
| connId | String | Connection ID |

Push Data

```
{
  "data": [{
    "unrealisedPnL": "-10116.55",
    "totalEquity": "4976919.05",
    "positionMgnRatio": "0",
    "mmr": "408.08",
    "effEquity": "4847952.35",
    "imr": "17795.97",
    "mgnRatio": "0",
    "coin": [{
      "debts": "0",
      "balance": "0.9992",
      "available": "0.9992",
      "borrow": "0",
      "locked": "0",
      "equity": "0.9992",
      "coin": "ETH",
      "usdValue": "2488.667472"
    }, {
      "debts": "0",
      "balance": "52.00819",
      "available": "52.00819",
      "borrow": "0",
      "locked": "0",
      "equity": "52.00819",
      "coin": "BTC",
      "usdValue": "4630564.31304974"
    }, {
      "debts": "0",
      "balance": "354411.45536458",
      "available": "344282.65536458",
      "borrow": "0",
      "locked": "0",
      "equity": "344282.65536458",
      "coin": "USDT",
      "usdValue": "343866.07335159"
    }]
  }],
  "arg": {
    "instType": "UTA",
    "topic": "account"
  },
  "action": "snapshot",
  "ts": 1740546523244
}
```

### Push Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| action | String | Action <br>`snapshot` Full data <br>`update` Incremental data |
| ts | Number | Timestamp |
| arg | Object | Subscribe channel |
| > instType | String | Product type<br> `UTA` |
| > topic | String | Topic<br>`account` |
| data | List | Subscribed data |
| > totalEquity | String | Account equity (USD) |
| > effEquity | String | Effective margin (USD)<br> The net value available for margin in spot and perpetual trades under cross-margin mode, converted to fiat |
| > mmr | String | Maintenance margin (USD) <br>The minimum margin required to maintain the position, converted to fiat |
| > imr | String | Initial margin (USD) Total initial margin of assets in base coin, converted to fiat |
| > mgnRatio | String | Margin ratio |
| >positionMgnRatio | String | Hold position margin ratio |
| > unrealisedPnL | String | Unrealised profit and loss |
| > coin | List | Coin list |
| >> coin | String | Coin name |
| >> balance | String | Coin balance |
| >> locked | String | Locked amount <br> *Only applicable for spot order placement* |
| >> equity | String | Coin equity (USD) |
| >> usdValue | String | Coin value (USD) |
| >> available | String | Available balance |
| >> borrow | String | Borrowed amount |
| >> debts | String | Debt <br> *Only applicable for margin trading* |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Account-Channel)


---

# Position

### Description

Data will be pushed when the following events occur:

1. Push on the first-time subscription
2. Push incremental data when close-position orders are placed in the unified trading account
3. Push incremental data when futures positions are opened in the unified trading account
4. Push incremental data when futures positions are closed in the unified trading account
5. Push incremental data when futures close-position orders are modified in the unified trading account
6. Push incremental data when futures close-position orders are cancelled in the unified trading account
Request

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "position"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| op | String | Operation <br> `subscribe` Subscribe <br>`unsubscribe` Unsubscribe |
| args | List<Object> | Subscribed channel |
| > instType | String | Product type <br>`UTA` Unified trading account |
| > topic | String | Topic `position` Position |

Response

```
{
  "event": "subscribe",
  "arg": {
    "instType": "UTA",
    "topic": "position"
  },
  "code": "",
  "msg": "",
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br>`subscribe` Subscription <br>`unsubscribe` Unsubscription <br>`error` Parameter error |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`UTA` Unified trading account |
| > topic | String | Topic <br>`position` Position |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "data": [
    {
      "symbol": "BTCUSDT",
      "leverage": "20",
      "openFeeTotal": "",
      "mmr": "",
      "breakEvenPrice": "",
      "available": "0",
      "liqPrice": "",
      "marginMode": "crossed",
      "unrealisedPnl": "0",
      "markPrice": "94987.1",
      "createdTime": "1736378720620",
      "avgPrice": "0",
      "totalFundingFee": "0",
      "updatedTime": "1736378720620",
      "marginCoin": "USDT",
      "frozen": "0",
      "profitRate": "",
      "closeFeeTotal": "",
      "marginSize": "0",
      "curRealisedPnl": "0",
      "size": "0",
      "positionStatus": "ended",
      "posSide": "long",
      "holdMode": "hedge_mode"
    }
  ],
  "arg": {
    "instType": "UTA",
    "topic": "position"
  },
  "action": "snapshot",
  "ts": 1730711666652
}
```

### Push Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`UTA` Unified Account |
| > topic | String | Topic <br>`position` Position channel |
| action | String | Action <br>`snapshot` Full data <br>`update` Incremental data |
| data | List<Object> | Subscribed data |
| > symbol | String | Symbol name |
| > marginCoin | String | Margin coin |
| > marginSize | String | Margin size |
| > marginMode | String | Margin mode<br>`crossed` Cross margin<br>`isolated` Isolated margin |
| > posSide | String | Position side <br>`long`/`short` |
| > holdMode | String | Holding mode <br> `one_way_mode`/`hedge_mode` |
| > positionStatus | String | Position status <br> `opening` Ongoing <br> `ended` Completed |
| > size | String | Position size <br>`size` = `available` + `frozen` |
| > available | String | Available position size |
| > frozen | String | Frozen position size |
| > avgPrice | String | Average open price |
| > leverage | String | Leverage multiple |
| > curRealisedPnl | String | Realised PnL |
| > unrealisedPnl | String | Unrealised PnL |
| > liqPrice | String | Estimated liquidation price |
| > mmr | String | Maintain margin rate |
| > marginRate | String | Margin rate |
| > markPrice | String | Mark Price |
| > openFeeTotal | String | Total opening fee |
| > closeFeeTotal | String | Total closing fee |
| > breakEvenPrice | String | Break-even price |
| > profitRate | String | Profit rate<br>*Profit rate = Unrealized PnL ÷ Initial margin*<br>*Initial margin = Average open price × Position size ÷ Leverage ÷ Margin coin index price* |
| > totalFundingFee | String | Total funding fee over the position's lifetime<br> `0` indicates that no funding fee has been charged yet |
| > createdTime | String | Position creation time<br>A Unix timestamp in milliseconds. e.g.,`1597026383085` |
| > updatedTime | String | Latest position update time<br>A Unix timestamp in milliseconds. e.g.,`1597026383085` |

## Profit Rate Calculation

**Profit Rate = Unrealized PnL / Initial Margin**

### Initial Margin

**Initial Margin = (Average Open Price × Position Size) / Leverage / Margin Coin Index Price**

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Positions-Channel)


---

# Order

### Description

The following events will trigger a data push:

1. No Push on first-time subscription
2. Push when spot/margin/futures orders are placed in the unified trading account
3. Push when spot/margin/futures orders are filled in the unified trading account
4. Push when spot/margin/futures orders are cancelled in the unified trading account
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "order"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| op | String | Operation <br> `subscribe`/`unsubscribe` |
| args | List<Object> | Subscribed channel |
| > instType | String | Product type <br> `UTA` Unified trading account |
| > topic | String | Topic <br>`order` Order |

Response Example

```
{
  "event": "subscribe",
  "arg": {
    "instType": "UTA",
    "topic": "order"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br> `subscribe` Subscribe <br> `unsubscribe` Unsubscribe <br> `error` Parameter error |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br> `UTA` Unified Account |
| > topic | String | Topic <br> `order` Order channel |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push

```
{
  "action": "snapshot",
  "arg": {
    "instType": "UTA",
    "topic": "order"
  },
  "data": [
    {
      "category": "usdt-futures",
      "symbol": "BTCUSDT",
      "orderId": "xxx",
      "clientOid": "xxx",
      "price": "",
      "qty": "0.001",
      "amount": "1000",
      "holdMode": "hedge_mode",
      "holdSide": "long",
      "delegateType": "normal",
      "tradeSide": "open",
      "orderType": "market",
      "timeInForce": "gtc",
      "side": "buy",
      "marginMode": "crossed",
      "marginCoin": "USDT",
      "reduceOnly": "no",
      "cumExecQty": "0.001",
      "cumExecValue": "83.1315",
      "avgPrice": "83131.5",
      "totalProfit": "0",
      "orderStatus": "filled",
      "cancelReason": "",
      "leverage": "20",
      "feeDetail": [
        {
          "feeCoin": "USDT",
          "fee": "0.0332526"
        }
      ],
      "createdTime": "1742367838101",
      "updatedTime": "1742367838115",
      "stpMode": "none"
    }
  ],
  "ts": 1742367838124
}
```

### Push Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type<br>`UTA` |
| > topic | String | Topic<br>`order` |
| action | String | Push data action<br>`snapshot` Full data |
| data | List<Object> | Subscribed data |
| > category | String | Business line <br>`spot` Spot trading<br>`margin` Margin trading<br> `usdt-futures` USDT Futures<br>`coin-futures` Coin-M futures<br>`usdc-futures` USDC Futures |
| > symbol | String | Symbol name |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| > price | String | Order price |
| > qty | String | Order quantity |
| > amount | String | Order amount |
| > holdMode | String | Holding mode<br>`one_way_mode` This mode allows holding positions in a single direction, either long or short, but not both at the same time <br>`hedge_mode` This mode allows holding both long and short positions simultaneously |
| > tradeSide | String | Trade side <br>`open`/`close` <br>Detailed enumerations can be obtained on the Enumeration page. |
| > orderType | String | Order type <br>`limit`/`market` |
| > timeInForce | String | Time in force <br> `ioc`: Immediate or cancel<br>`fok`: Fill or kill <br>`gtc`: Good 'till cancelled<br> `post_only`: Post (maker) only<br>`rpi`: Retail Price Improvement order |
| > side | String | Order side <br> `buy`/`sell` |
| > holdSide | String | Position side <br> `long` Long position<br>`short` Short position |
| > delegateType | String | Delegate type<br>`normal`: Limit Order<br> `stop_profit_market`: Take Profit Market<br> `stop_loss_market`: Stop Loss Market<br> `stop_profit_chase`: Take Profit Chase Order<br> `stop_loss_chase`: Stop Loss Chase Order<br> `trader_delegate`: Lead Trader Order<br> `trader_stop_profit`: Lead Trader Take Profit<br> `trader_stop_loss`: Lead Trader Stop Loss<br> `follower_delegate`: Follower Order<br> `reduce_offset_delegate`: Reduce-Only Offset Order<br> `market`: Market Order<br> `market_risk`: Market Order (Risk Handling)<br> `plan_limit`: Limit Conditional Order<br> `plan_market`: Market Conditional Order<br> `back_contract`: Reverse Position<br> `trader_back_contract`: Lead Trader Reverse Position<br> `strategy_grid_positive`: Strategy - Long Grid<br> `strategy_grid_reverse`: Strategy - Short Grid<br> `strategy_unlimited`: Unlimited Strategy<br> `stop_profit_limit`: Take Profit Limit<br> `stop_loss_limit`: Stop Loss Limit<br> `move_stop_limit`: Trailing Stop Limit<br> `move_stop_market`: Trailing Stop Market<br> `position_stop_profit_limit`: Position Take Profit Limit<br> `position_stop_profit_market`: Position Take Profit Market<br> `position_stop_loss_limit`: Position Stop Loss Limit<br> `position_stop_loss_market`: Position Stop Loss Market<br> `tracking_plan_limit`: Trailing Limit Order<br> `tracking_plan_market`: Trailing Market Order<br> `delivery_close_long`: Long Delivery Close<br> `delivery_close_short`: Short Delivery Close<br> `liquidation`: Liquidation<br> `strategy_dca_positive`: DCA Strategy - Long<br> `strategy_dca_reverse`: DCA Strategy - Short<br> `spot_trace_trader_buy`: Spot Lead Trader Buy<br> `spot_trace_follower_buy`: Spot Follower Buy<br> `spot_trace_trader_sell`: Spot Lead Trader Sell<br> `spot_trace_follower_sell`: Spot Follower Sell<br> `strategy_oco_limit`: Strategy - OCO Limit Order<br> `strategy_oco_trigger`: Strategy - OCO Trigger Order<br> `modify_limit_order`: Modify Limit Order<br> `strategy_regular_buy`: Strategy - Auto-Invest Buy<br> `strategy_grid_middle`: Strategy - Neutral Grid<br> `strategy_cta_positive`: CTA Strategy - Long<br> `strategy_cta_reverse`: CTA Strategy - Short<br> `strategy_tpsl_limit`: Spot TP/SL Limit Order<br> `strategy_tpsl_market`: Spot TP/SL Market Order<br> `strategy_contract_ai`: Futures AI Investment Strategy<br> `strategy_trace_market`: Trailing Stop Market Order<br> `strategy_trace_limit`: Trailing Stop Limit Order<br> `strategy_portfolio_buy`: Strategy - Smart Portfolio Buy<br> `strategy_portfolio_sell`: Strategy - Smart Portfolio Sell<br> `strategy_tradingview`: TradingView Signal Strategy<br> `sigan_trace`: Signal Follower<br> `mmr_stop_loss_market`: MMR Stop Loss Market<br> `bbo_opponent1`: BBO - Opponent Best Price 1<br> `bbo_opponent5`: BBO - Opponent Best Price 5<br> `bbo_companion1`: BBO - Companion Best Price 1<br> `bbo_companion5`: BBO - Companion Best Price 5<br> `bbo_opponent1_profit`: BBO - Opponent 1 Take Profit<br> `bbo_opponent5_profit`: BBO - Opponent 5 Take Profit<br> `bbo_companion1_profit`: BBO - Companion 1 Take Profit<br> `bbo_companion5_profit`: BBO - Companion 5 Take Profit<br> `bbo_opponent1_loss`: BBO - Opponent 1 Stop Loss<br> `bbo_opponent5_loss`: BBO - Opponent 5 Stop Loss<br> `bbo_companion1_loss`: BBO - Companion 1 Stop Loss<br> `bbo_companion5_loss`: BBO - Companion 5 Stop Loss<br> `spot_bbo_opponent1_tpsl`: Spot BBO - Opponent 1 TP/SL<br> `spot_bbo_opponent5_tpsl`: Spot BBO - Opponent 5 TP/SL<br> `spot_bbo_companion1_tpsl`: Spot BBO - Companion 1 TP/SL<br> `spot_bbo_companion5_tpsl`: Spot BBO - Companion 5 TP/SL<br> `dummy_bbo_profit`: BBO - Take Profit<br> `dummy_bbo_loss`: BBO - Stop Loss<br> `strategy_pre_tpsl_limit`: Spot Preset TP/SL Limit Order<br> `strategy_pre_tpsl_market`: Spot Preset TP/SL Market Order<br> `future_signal_delegate`: Futures Copy Trading Limit Order<br> `grant_market`: Voucher Opening Order<br> `tg_signal_limit`: TG Signal Limit Order<br> `tg_signal_tp_market`: TG Signal Take Profit Market<br> `tg_signal_sl_market`: TG Signal Stop Loss Market<br> `strategy_preset_tpsl_limit`: Spot Preset Trigger TP/SL Limit Order<br> `strategy_preset_tpsl_market`: Spot Preset Trigger TP/SL Market Order<br> `trader_iceberg_limit`: Iceberg Order<br> `trader_time_share_market`: TWAP Market Order<br> `strategy_arbitrage_positive`: Funding Rate Arbitrage Strategy - Long<br> `strategy_arbitrage_reverse`: Funding Rate Arbitrage Strategy - Short <br> `convert_hedging`: Convert Hedging<br> `off_close`: Delisting Close<br> |
| > marginMode | String | Margin mode <br>`crossed` Cross margin <br> `isolated` Isolated margin |
| > reduceOnly | String | Reduce-only identifier<br>`yes`/`no` |
| > marginCoin | String | Margin coin |
| > cumExecQty | String | Cumulative executed quantity |
| > cumExecValue | String | Cumulative executed value |
| > avgPrice | String | Average execution price <br>If not executed, this field will default to 0 |
| > totalProfit | String | Total profit |
| > orderStatus | String | Order status<br>`new` Order matching. <br>`partially_filled` Partially filled<br>`filled` Fully filled<br>`cancelled` Cancelled |
| > cancelReason | String | Reason for order cancellation |
| > leverage | String | Leverage |
| > feeDetail | List | Fee detail list |
| >> feeCoin | String | Fee coin |
| >> fee | String | Fee amount |
| > createdTime | String | Created time<br>A Unix timestamp in milliseconds. e.g.,`1597026383085` |
| > updatedTime | String | Updated time<br>A Unix timestamp in milliseconds. e.g.,`1597026383085` |
| > stpMode | String | STP Mode(Self Trade Prevention)<br> `none`: no STP settings<br>`cancel_taker`: cancel taker order <br>`cancel_maker`: cancel maker order <br>`cancel_both`: cancel both of taker and maker orders |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Order-Channel)


---

# Fill

### Description

Push real-time fill data.

Push rules:

1. No push on first-time subscription
2. Push when spot/leveraged/futures orders are filled
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "fill"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| op | String | Operation: <br> `subscribe` /`unsubscribe` |
| args | List<Object> | Subscribed channel |
| > instType | String | Product type<br>`UTA` Unified trading account |
| > topic | String | Topic <br>`fill` |

Response Example

```
{
    "event": "subscribe",
    "arg": {
        "instType": "UTA",
        "topic": "fill"
    },
    "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br>`subscribe` / `unsubscribe` / `error` |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`UTA` |
| > topic | String | Topic <br>`fill` |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push Data

```
{
  "data": [
    {
      "symbol": "BTCUSDT",
      "orderType": "market",
      "updatedTime": "1736378720623",
      "side": "buy",
      "orderId": "1288888888888888888",
      "execPnl": "0",
      "feeDetail": [
        {
          "feeCoin": "USDT",
          "fee": "0.569958"
        }
      ],
      "execTime": "1736378720623",
      "tradeScope": "taker",
      "tradeSide": "open",
      "execId": "1288888888888888888",
      "execLinkId": "1288888888888888888",
      "execPrice": "94993",
      "holdSide": "long",
      "execValue": "949.93",
      "category": "usdt-futures",
      "execQty": "0.01",
      "clientOid": "1288888888888888889",
      "isRPI": "no"
    }
  ],
  "arg": {
    "instType": "UTA",
    "topic": "fill"
  },
  "action": "snapshot",
  "ts": 1733904123981
}
```

### Push Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | product type <br>`UTA` Unified trading account |
| > topic | String | Topic <br>`fill` fill |
| action | String | Action <br>`snapshot` Full data |
| data | List<Object> | Subscribed data |
| > category | String | Product type <br> `spot` Spot trading <br> `margin` Margin trading <br> `usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |
| > orderId | String | Order ID |
| > clientOid | String | Client Order ID |
| > execId | String | Fill ID |
| > execLinkId | String | Execution correlation ID |
| > symbol | String | Symbol name |
| > orderType | String | Order type <br>`limit`/`market` |
| > side | String | Trade side<br>`buy`/`sell` |
| > holdSide | String | Position direction <br>`long`: Long <br>`short`: Short |
| > tradeSide | String | Trade side <br>`open`/`close` <br>Detailed enumerations can be obtained on the Enumeration page. |
| > execPrice | String | Fill price |
| > execQty | String | Fill quantity<br>*The unit is base coin* |
| > execValue | String | Fill value<br>*The unit is quote coin* |
| > execPnl | String | Execution profit and loss |
| > tradeScope | String | Trade scope<br>`taker`/`maker` |
| > feeDetail | String | Fee detail |
| >> feeCoin | String | Fee coin |
| >> fee | String | Total fee |
| > execTime | String | Execution timestamp <br>A Unix timestamp in milliseconds. e.g.,`1622697148123` |
| > updatedTime | String | Updated timestamp<br>A Unix timestamp in milliseconds. e.g.,`1622697148123` |
| > isRPI | String | Whether it is an RPI fill<br>`yes` Yes<br>`no` No |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Fill-Channel)


---

# Fast Fill

### Description

This channel only pushes order fill data for users in UTA mode.
 This channel does not currently push strategy and copy trading fill data.

Push rules:

1. No push on first-time subscription
2. Push when spot/leveraged/futures orders are filled in the unified trading account
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "fast-fill",
            "symbol": "default"
        }
    ]
}
```

### Request Parameters

| Parameters | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `subscribe` /`unsubscribe` |
| args | List<Object> | Yes | Subscribed channel list |
| > instType | String | Yes | Product type<br>`UTA` Unified trading account |
| > topic | String | Yes | Topic <br>`fast-fill` Fast fill |
| > symbol | String | No | Symbol name<br>`default` All symbols |

Response Example

```
{
    "event": "subscribe",
    "arg": {
        "instType": "UTA",
        "topic": "fast-fill"
    },
    "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br>`subscribe` / `unsubscribe` / `error` |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`UTA` Unified trading account |
| > topic | String | Topic <br>`fast-fill` Fast fill |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push Data

```
{
  "data":
    {
      "symbol": "BTCUSDT",
      "updatedTime": "1736378720623",
      "side": "buy",
      "orderId": "1288888888888888888",
      "execTime": "1736378720623",
      "tradeScope": "taker",
      "execId": "1288888888888888888",
      "execPrice": "94993",
      "holdSide": "long",
      "category": "usdt-futures",
      "execQty": "0.01",
      "clientOid": "1288888888888888889"
    },
  "arg": {
    "instType": "UTA",
    "topic": "fast-fill"
  },
  "action": "update",
  "ts": 1736378720653
}
```

### Push Parameters

| Parameters | Type | Description | Remarks |
| --- | --- | --- | --- |
| arg | Object | Subscribed channel |  |
| > instType | String | Product type <br>`UTA` Unified trading account |  |
| > topic | String | Topic <br>`fast-fill` Fast fill |  |
| action | String | Push data action<br>`update` Incremental |  |
| data | Object | Subscribed data |  |
| > category | String | Business line <br> `spot` Spot trading <br> `margin` Margin trading <br> `usdt-futures` USDT futures <br> `coin-futures` Coin-M futures <br> `usdc-futures` USDC futures |  |
| > orderId | String | Order ID |  |
| > clientOid | String | Client Order ID |  |
| > symbol | String | Symbol name |  |
| > execId | String | Fill ID |  |
| > side | String | Trade side<br>`buy` Buy<br>`sell` Sell |  |
| > holdSide | String | Position direction <br>`long` Long <br>`short` Short |  |
| > execPrice | String | Fill price |  |
| > execQty | String | Fill quantity |  |
| > tradeScope | String | Trade scope<br>`taker` Taker<br>`maker` Maker |  |
| > execTime | String | Execution timestamp <br>A Unix timestamp in milliseconds. e.g.,`1622697148123` |  |
| > updatedTime | String | Updated timestamp<br>A Unix timestamp in milliseconds. e.g.,`1622697148123` |  |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Fast-Fill-Channel)


---

# Place Order

### Description

This endpoint allows order placement across spot, margin, or futures markets with customizable parameters, including price, quantity, and order type, etc.

- Futures For one-way mode, reduce-only orders are allowed to place. If a reduce only order already exists and the order quantity equals the position size, or if a new reduce only order exceeds the remaining position size, the previous reduction order will be automatically canceled and replaced. In this case, the returned orderId will be null. It is recommended to always provide a clientOid.
- Margin Margin orders will automatically trigger fund borrowing.
- Order Check
  - Futures:price must meet the price multiplier and be a multiple of priceMultiplier, and conform to the pricePrecision decimal places. qty must be greater than or equal to minOrderAmount and be a multiple of sizeMultiplier.
  - Spot:price must meet the decimal place requirement. qty must be greater than or equal to minOrderAmount.

- Open Position Logic
  - Hedge-mode Open long: side=buy & posSide=long Open Short: side=sell & posSide=short Close long: side=sell & posSide=long Close short: side=buy & posSide=short
  - One-way-mode Open long: side=buy Open short: side=sell Close long: side=sell & reduceOnly=yes Close short: side=buy & reduceOnly=yes

- Order Limit
  - Futures: 400 orders across all USDT, Coin-M, and USDC futures trading pairs.
  - Spot: 400 orders across all spot and margin trading pairs.

- ClientOid Constraints Please ensure your clientOid matches the regular expression `^[0-9A-Za-z_:#\\-+\\s]{1,32}$`, consisting of 1 to 32 characters, including periods (.), uppercase letters, colons (:), lowercase letters, numbers, underscores (_), and hyphens (-).
- Request Monitor The API requests will be monitored. If the total number of orders for a single account (including master and sub-accounts) exceeds a set daily limit (UTC 00:00 - UTC 24:00), the platform reserves the right to issue reminders, warnings, and enforce necessary restrictions. By using the API, clients acknowledge and agree to comply with these terms.
- Error Sample { "code":"40762", "msg":"The order size is greater than the max open size", "requestTime":1627293504612 } This error code may occur in the following scenarios.
  - Insufficient account balance.
  - The position tier for this symbol has reached its limit. Position tiers

- Note: If the following errors occur when placing an order, please use clientOid to query the order details to confirm the final result of the operation.
- **COIN-M Futures Symbol Format Description:**
  - The symbol format for the new COIN-M business line is "XXXUSD_CM". For example, the BTCUSD trading pair in COIN-M futures is formatted as BTCUSD_CM.
  - The new COIN-M business line does not support modifying orders, ADL, strategy orders，or preset take-profit/stop-loss orders.

> { "code": "40010", "msg": "Request timed out", "id": 1666268894074, "event":"error" }
>  { "code": "40725", "msg": "service return an error", "id": 1666268894071, "event":"error" }
>  { "code": "45001", "msg": "Unknown error", "id": 1666268894071, "event":"error" }
Request Example

```
{
  "op": "trade",
  "id": "1750034396082",
  "category": "spot",
  "topic": "place-order",
  "args": [
    {
      "orderType": "limit",
      "price": "100",
      "qty": "0.1",
      "side": "buy",
      "symbol": "BTCUSDT",
      "timeInForce": "gtc",
    }
  ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` |
| id | String | Yes | Request identifier |
| topic | String | Yes | Topic: <br> `place-order` |
| category | String | Yes | Category <br>`spot`Spot<br>`margin` Margin<br> `usdt-futures` USDT futures<br>`coin-futures` Coin futures<br>`usdc-futures` USDC futures |
| apiCode | String | No | API rebate identifier |
| args | List<Object> | Yes | Channel list |
| > symbol | String | Yes | Symbol name |
| > orderType | String | Yes | Order type <br>`limit` : Limit order<br> `market` : Market order |
| > qty | String | Yes | Order quantity <br>`Spot/Margin`<br> For market buy orders,the unit is **quote coin**<br>For limit and market sell orders, the unit is **base coin**<br> `USDT/USDC-Futures` <br> The unit is **base coin** <br> `COIN-Futures` <br> The unit is **quote coin** |
| > price | String | No | Order price <br> This field is required when the order type is a `limit` order .<br>This field is not applicable when the order type is a `market` order. |
| > side | String | Yes | Order side<br>`buy`<br>`sell` |
| > posSide | String | No | Position side<br>`long` <br>`short` <br>This field is required in hedge-mode positions.<br>Available only for futures |
| > timeInForce | String | No | Time in force <br> `gtc`: Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`ioc`: Immediate or cancel. It must be executed immediately, with any unfilled portion canceled.<br>`fok`: Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br> `post_only`: Post only. It will only be added to the order book as a maker. <br>`rpi` Retail Price Improvement order. A non-displayed limit order that provides price improvement for retail order flow. Only available for accounts with RPI market maker permissions.<br>This field is required when orderType is `limit`. If omitted, it defaults to `gtc` |
| > reduceOnly | String | No | Reduce-only identifier<br>`YES`/`NO`<br>default`NO`; `YES` indicates that your position may only be reduced in size upon the activation of this order |
| > clientOid | String | No | Client order ID |
| > stpMode | String | No | STP Mode(Self Trade Prevention)<br>`none`： no STP settings(Default)<br>`cancel_taker`：cancel taker order <br>`cancel_maker`：cancel maker order <br>`cancel_both`：cancel both of taker and maker orders |
| > tpTriggerBy | String | No | Preset Take-Profit Trigger Type<br>`market`Market Price<br>`mark` Mark Price<br>If not specified, the default value is market price <br> Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures. |
| > slTriggerBy | String | No | Preset Stop-Loss Trigger Type<br>`market`Market Price<br>`mark` Mark Price<br>If not specified, the default value is market price <br> Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures. |
| > takeprofit | String | No | Preset Take-Profit Trigger Price |
| > stoploss | String | No | Preset Stop-Loss Trigger Price |
| > tpOrderType | String | No | Take-Profit Trigger Strategy Order Type<br>`limit` Limit Order<br>`market` Market Order |
| > slOrderType | String | No | Stop-Loss Trigger Strategy Order Type<br>`limit` Limit Order<br>`market` Market Order |
| > tpLimitPrice | String | No | Take-Profit Strategy Order Execution Price<br>This field is only valid for limit orders (when `tpOrderType=limit`); it is ignored for market orders. |
| > slLimitPrice | String | No | Stop-Loss Strategy Order Execution Price<br>This field is only valid for limit orders (when `slOrderType=limit`); it is ignored for market orders. |
| > marginMode | String | No | Margin mode<br>`crossed` Cross margin<br>`isolated` Isolated margin<br>If not provided, defaults to cross margin<br>Available only for futures |

Response

```
{
  "event": "trade",
  "id": "1750034396082",
  "category": "spot",
  "topic": "place-order",
  "args": [
    {
      "symbol": "BTCUSDT",
      "orderId": "xxxxxxxx",
      "clientOid": "xxxxxxxx",
      "cTime": "1750034397008"
    }
  ],
  "code": "0",
  "msg": "success",
  "connId": "xxxxxxxxxx",
  "ts": "1750034397076"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic<br>`place-order` |
| category | String | Category <br>`spot`Spot<br>`margin` Margin<br> `usdt-futures` USDT futures<br>`coin-futures` Coin futures<br>`usdc-futures` USDC futures |
| args | List<Object> | Channel list |
| > cTime | String | Order creation time <br>Unix millisecond timestamp |
| > symbol | String | Symbol name |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Place-Order-Channel)


---

# Cancel Order

### Description
Request Example

```
{
    "args": [
        {
            "orderId": "xxxxxxxxxxxxxxxxxx",
            "clientOid": "xxxxxxxxxxxxxxxxxx"
        }
    ],
    "id": "c8a1999c-1f82-409d-870e-f40ff49c4072",
    "op": "trade",
    "topic": "cancel-order"
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` trade |
| id | String | Yes | Request identifier |
| topic | String | Yes | topic<br>`cancel-order` |
| category | String | No | Category <br>`spot`Spot<br>`margin` Margin<br> `usdt-futures` USDT futures<br>`coin-futures` Coin futures<br>`usdc-futures` USDC futures |
| args | List<Object> | Yes | Channel list |
| > orderId | String | No | Order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| > clientOid | String | No | Client order ID <br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |

Response

```
{
  "event": "trade",
  "id": "1750034870205",
  "topic": "cancel-order",
  "args": [
    {
      "orderId": "xxxxxxxx",
      "clientOid": "xxxxxxxx"
    }
  ],
  "code": "0",
  "msg": "Success",
  "connId": "xxxxxxxxxx",
  "ts": "1750034870597"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic<br>`cancel-order` cancel-order |
| args | List<Object> | Channel list |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Cancel-Order-Channel)


---

# Modify Order

### Description

**Asynchronous Order Modification:**

- Order modifications are executed asynchronously. Even after receiving a successful acknowledgment (ACK) response, the final modification may still fail.
- No additional notification will be sent in case of failure, but you may receive order fill or cancellation updates (e.g., if the order is canceled or fully filled before the modification request takes effect).
- It is recommended to query the current order status via REST API to confirm whether the modification was successful: If the order still exists, you can retry; if it does not exist, it indicates the order has been filled or canceled, and no further action is needed.
Request

```
{
    "args": [
        {
            "autoCancel": "yes",
            "clientOid": "135423791666666666",
            "orderId": "1354237910666666666",
            "price": "5",
            "qty": "2",
            "symbol":"BTCUSDT"
        }
    ],
    "id": "ae5ea6df-215f-4750-a700-d487d03ac020",
    "op": "trade",
    "category": "usdt-futures",
    "topic": "modify-order"
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` |
| id | String | Yes | Request identifier |
| topic | String | Yes | Topic<br> `modify-order` |
| category | String | No | Business line <br>`spot` Spot trading<br>`margin` Margin trading<br> `usdt-futures` USDT Futures<br>`coin-futures` Coin-M futures<br>`usdc-futures` USDC Futures |
| args | List<Object> | Yes | Channel list |
| > orderId | String | No | Order ID <br>Either orderId or clientOid is required.<br>If both orderId and clientOid are passed simultaneously, orderId takes higher priority, and the clientOid parameter will be ignored. |
| > clientOid | String | No | Client order ID<br>Either orderId or clientOid is required.<br>If both orderId and clientOid are passed simultaneously, orderId takes higher priority, and the clientOid parameter will be ignored. |
| > qty | String | No | Order quantity<br>- Spot<br> For market buy orders, the unit is quote coin <br>For limit and market sell orders, the unit is base coin <br>- Futures<br>The unit is base coin |
| > price | String | No | Order price<br>This field is required when orderType is `limit` |
| > autoCancel | String | No | Will the original order be canceled if the order modification fails<br>`yes`: Cancel <br>`no`: Not cancel（default）<br>When set to `yes`: if the matching engine fails to modify the order, the order is cancelled immediately; after cancellation, the counter will reject any further modification requests for that order (including in-flight and new requests). |
| > symbol | String | No | Symbol name |

Response

```
{
  "event": "trade",
  "id": "ae5ea6df-215f-4750-a700-d487d03ac020",
  "topic": "modify-order",
  "args": [
    {
      "orderId": "135423791666666666",
      "clientOid": "1354237910666666666"
    }
  ],
  "code": "0",
  "msg": "Success",
  "connId": "xxxxxxxxxx",
  "ts": "1758601481031"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic<br>`modify-order` |
| args | List<Object> | Channel list |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Modify-Order-Channel)


---

# Batch Place Order Channel

### Description

- Maximum 20 orders allowed in a single request
Request Example

```
{
    "op": "trade",
    "id": "1750035029506",
    "category": "spot",
    "topic": "batch-place",
    "args": [
        {
            "clientOid": "xxxxxxxx",
            "orderType": "limit",
            "price": "100",
            "qty": "0.1",
            "side": "buy",
            "symbol": "BTCUSDT",
            "timeInForce": "gtc"
        },
        {
            "clientOid": "xxxxxxxx",
            "orderType": "limit",
            "price": "100",
            "qty": "0.15",
            "side": "buy",
            "symbol": "BTCUSDT",
            "timeInForce": "gtc"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` |
| id | String | Yes | Request identifier |
| topic | String | Yes | Topic: <br> `batch-place` |
| category | String | Yes | Category <br>`spot`Spot<br>`margin` Margin<br> `usdt-futures` USDT futures<br>`coin-futures` Coin futures<br>`usdc-futures` USDC futures |
| args | List<Object> | Yes | Channel list |
| > symbol | String | Yes | Symbol name |
| > orderType | String | Yes | Order type <br>`limit` : Limit order<br> `market` : Market order |
| > qty | String | Yes | Order quantity <br>`Spot/Margin`<br> For market buy orders,the unit is **quote coin**<br>For limit and market sell orders, the unit is **base coin**<br> `USDT/USDC-Futures` <br> The unit is **base coin** <br> `COIN-Futures` <br> The unit is **quote coin** |
| > price | String | No | Order price<br>This field is required when the order type is a `limit` order .<br>This field is not applicable when the order type is a `market` order. |
| > side | String | Yes | Order side<br>`buy`<br>`sell` |
| > posSide | String | No | Position side<br>`long` <br>`short` <br>This field is required in hedge-mode positions.<br>Available only for futures |
| > timeInForce | String | No | Time in force <br> `gtc`: Good 'til canceled. It remains active until it is either filled or manually canceled.<br>`ioc`: Immediate or cancel. It must be executed immediately, with any unfilled portion canceled.<br>`fok`: Fill or kill. It must be fully executed immediately, or it is canceled entirely. <br> `post_only`: Post only. It will only be added to the order book as a maker. <br>`rpi` Retail Price Improvement order. A non-displayed limit order that provides price improvement for retail order flow. Only available for accounts with RPI market maker permissions. <br>This field is required when orderType is `limit`. If omitted, it defaults to `gtc` |
| > clientOid | String | No | Client order ID |
| > stpMode | String | No | STP Mode(Self Trade Prevention)<br>`none`： no STP settings(Default)<br>`cancel_taker`：cancel taker order <br>`cancel_maker`：cancel maker order <br>`cancel_both`：cancel both of taker and maker orders |
| > tpTriggerBy | String | No | Preset Take-Profit Trigger Type<br>`market`Market Price<br>`mark` Mark Price<br>If not specified, the default value is market price <br> Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures. |
| > slTriggerBy | String | No | Preset Stop-Loss Trigger Type<br>`market`Market Price<br>`mark` Mark Price<br>If not specified, the default value is market price <br> Note: This field is only valid for the contract business lines: USDT-Futures, COIN-Futures, and USDC-Futures. |
| > takeprofit | String | No | Preset Take-Profit Trigger Price |
| > stoploss | String | No | Preset Stop-Loss Trigger Price |
| > tpOrderType | String | No | Take-Profit Trigger Strategy Order Type<br>`limit` Limit Order<br>`market` Market Order |
| > slOrderType | String | No | Stop-Loss Trigger Strategy Order Type<br>`limit` Limit Order<br>`market` Market Order |
| > tpLimitPrice | String | No | Take-Profit Strategy Order Execution Price<br>This field is only valid for limit orders (when `tpOrderType=limit`); it is ignored for market orders. |
| > slLimitPrice | String | No | Stop-Loss Strategy Order Execution Price<br>This field is only valid for limit orders (when `slOrderType=limit`); it is ignored for market orders. |

Response

```
{
  "event": "trade",
  "id": "1750035029506",
  "category": "spot",
  "topic": "batch-place",
  "args": [
    {
      "code": "0",
      "msg": "Success",
      "symbol": "BTCUSDT",
      "orderId": "xxxxxxxx",
      "clientOid": "xxxxxxxx"
    },
    {
      "code": "0",
      "msg": "Success",
      "symbol": "BTCUSDT",
      "orderId": "xxxxxxxx",
      "clientOid": "xxxxxxxx"
    }
  ],
  "code": "0",
  "msg": "Success",
  "connId": "xxxxxxxxxx",
  "ts": "1750035029925"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic: <br> `batch-place` |
| category | String | Category <br>`spot`Spot<br>`margin` Margin<br> `usdt-futures` USDT futures<br>`coin-futures` Coin futures<br>`usdc-futures` USDC futures |
| args | List<Object> | Channel list |
| > symbol | String | Symbol name |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| > code | String | Code |
| > msg | String | Message |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Batch-Place-Order-Channel)


---

# Batch Cancel Order

### Description

- Batch order cancellation allows partial success.
- In batch order cancellation, ensure that each call uses only orderId or clientOid for identification, and avoid mixing them. If orderId and clientOid are used together in one call, clientOid will be invalid.
Request Example

```
{
    "args": [
        {
            "orderId": "xxxxxxxx"
        },
        {
            "orderId": "xxxxxxxx"
        }
    ],
    "id": "xxxxx-xxx-xxx-xxxx-xxxxxx",
    "op": "trade",
    "topic": "batch-cancel"
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` trade |
| id | String | Yes | Request identifier |
| topic | String | Yes | Topic<br>`batch-cancel` |
| args | List<Object> | Yes | Channel list |
| > orderId | String | No | Order ID<br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |
| > clientOid | String | No | Client order ID <br>Either `clientOid` or `orderId` must be provided. If both are present or do not match, `orderId` will take priority |

Response Example

```
{
  "event": "trade",
  "id": "bb553cc0-c1fa-454e-956d-c96c8d715760",
  "topic": "batch-cancel",
  "args": [
    {
      "code": "0",
      "msg": "Success",
      "orderId": "xxxxxxxxxxxxx",
      "clientOid": "xxxxxxxxxxxxx"
    },
    {
      "code": "25204",
      "msg": "Order does not exist",
      "orderId": "xxxxxxxxxxxxx",
      "clientOid": "xxxxxxxxxxxxx"
    }
  ],
  "code": "0",
  "msg": "Success",
  "connId": "xxxxxxxxxx",
  "ts": "1751980011084"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic<br>`batch-cancel` |
| args | List<Object> | Channel list |
| > code | String | Code |
| > msg | String | Message |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Batch-Cancel-Order-Channel)


---

# Batch Modify Order

### Description
Request

```
{
    "args": [
        {
            "autoCancel": "yes",
            "clientOid": "1354237913333333333",
            "orderId": "1354237910333333333",
            "price": "5",
            "qty": "5"
        },
        {
            "autoCancel": "yes",
            "clientOid": "1354240301222222222",
            "orderId": "1354240301222222222",
            "price": "5",
            "qty": "2"
        }
    ],
    "id": "63210d1e-b400-47c7-afc6-323018afe71a",
    "op": "trade",
    "topic": "batch-modify"
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation: <br> `trade` |
| id | String | Yes | Request identifier |
| topic | String | Yes | Topic<br> `batch-modify` |
| category | String | No | Business line <br>`spot` Spot trading<br>`margin` Margin trading<br> `usdt-futures` USDT Futures<br>`coin-futures` Coin-M futures<br>`usdc-futures` USDC Futures |
| args | List<Object> | Yes | Channel list |
| > orderId | String | No | Order ID <br>Either orderId or clientOid is required.<br>If both orderId and clientOid are passed simultaneously, orderId takes higher priority, and the clientOid parameter will be ignored. |
| > clientOid | String | No | Client order ID<br>Either orderId or clientOid is required.<br>If both orderId and clientOid are passed simultaneously, orderId takes higher priority, and the clientOid parameter will be ignored. |
| > qty | String | No | Order quantity<br>- Spot<br> For market buy orders, the unit is quote coin <br>For limit and market sell orders, the unit is base coin <br>- Futures<br>The unit is base coin |
| > price | String | No | Order price<br>This field is required when orderType is `limit` |
| > autoCancel | String | No | Will the original order be canceled if the order modification fails<br>`yes`: Cancel <br>`no`: Not cancel（default）<br>When set to `yes`: if the matching engine fails to modify the order, the order is cancelled immediately; after cancellation, the counter will reject any further modification requests for that order (including in-flight and new requests). |
| > symbol | String | No | Symbol name |

Response

```
{
  "event": "trade",
  "id": "63210d1e-b400-47c7-afc6-323018afe71a",
  "topic": "batch-modify",
  "args": [
    {
      "code": "25571",
      "msg": "The modification price and qty is the same as the original value. Please adjust and try again.",
      "orderId": "1354237913333333333",
      "clientOid": "1354237910333333333"
    },
    {
      "code": "0",
      "msg": "Success",
      "orderId": "1354240301222222222",
      "clientOid": "1354240301222222222"
    }
  ],
  "code": "0",
  "msg": "Success",
  "connId": "xxxxxxxxxx",
  "ts": "1758602036638"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event<br>`trade`/`error` |
| id | String | Request identifier |
| topic | String | Topic<br>`batch-modify` |
| args | List<Object> | Channel list |
| > orderId | String | Order ID |
| > clientOid | String | Client order ID |
| > code | String | Code |
| > msg | String | Message |
| code | String | Code |
| msg | String | Message |
| connId | String | Connection ID |
| ts | String | Timestamp |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Batch-Modify-Order-Channel)


---

# Strategy Order Channel

### Description

The following events will trigger a data push:

1. No push on first-time subscription
2. Push when a strategy order is placed in the unified trading account
3. Push when a strategy order status changes in the unified trading account
4. Push when a strategy order is cancelled in the unified trading account
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "strategy-order",
            "symbol": "default"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe` / `unsubscribe` |
| args | List<Object> | Yes | Subscribed channel list |
| > instType | String | Yes | Product type <br> `UTA` Unified trading account |
| > topic | String | Yes | Topic <br>`strategy-order` Strategy order |
| > symbol | String | No | Symbol name<br>Currently only `default` is supported (all symbols) |

Response Example

```
{
  "event": "subscribe",
  "arg": {
    "instType": "UTA",
    "topic": "strategy-order",
    "symbol": "default"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Event <br>`subscribe` / `unsubscribe` / `error` |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br> `UTA` Unified trading account |
| > topic | String | Topic <br>`strategy-order` Strategy order |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push Data

```
{
  "action": "snapshot",
  "arg": {
    "instType": "UTA",
    "topic": "strategy-order",
    "symbol": "default"
  },
  "data": [
    {
      "category": "usdt-futures",
      "symbol": "BTCUSDT",
      "orderId": "111111111111111111",
      "clientOid": "myorder001",
      "qty": "0.01",
      "side": "buy",
      "posSide": "long",
      "reduceOnly": "no",
      "type": "tpsl",
      "status": "pending",
      "triggerType": "takeProfit",
      "tpTriggerBy": "market",
      "slTriggerBy": "market",
      "takeProfit": "110000",
      "stopLoss": "90000",
      "tpOrderType": "market",
      "slOrderType": "market",
      "tpLimitPrice": "",
      "slLimitPrice": "",
      "triggerBy": "market",
      "triggerPrice": "100000",
      "triggerOrderType": "limit",
      "triggerOrderPrice": "100500",
      "createdTime": "1730186725663",
      "updatedTime": "1730186725691"
    }
  ],
  "ts": 1730186725700
}
```

### Push Parameters

| Return Field | Parameter Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br> `UTA` Unified trading account |
| > topic | String | Topic <br>`strategy-order` Strategy order |
| action | String | Push data action <br>`snapshot` Full data |
| ts | String | Push timestamp (milliseconds) |
| data | List<Object> | Push data |
| > category | String | Product type <br>`usdt-futures` USDT futures <br>`coin-futures` Coin-M futures <br>`usdc-futures` USDC futures |
| > symbol | String | Symbol name |
| > orderId | String | Strategy order ID |
| > clientOid | String | Client strategy order ID |
| > qty | String | Order quantity |
| > side | String | Trade side <br>`buy` / `sell` |
| > posSide | String | Position side <br>`long` / `short` |
| > reduceOnly | String | Whether it is reduce-only |
| > type | String | Strategy type <br>`tpsl` Take-Profit and Stop-Loss <br>`trigger` Trigger Order |
| > status | String | Strategy order status <br>`pending` Waiting to be executed <br>`success` Executed <br>`failed` Execution failed <br>`cancelled` Cancelled <br>`submitting` Submitting |
| > triggerType | String | Trigger type <br>`takeProfit` Take-Profit <br>`stopLoss` Stop-Loss |
| > tpTriggerBy | String | Take-Profit trigger price type <br>`market`: Market Price <br>`mark`: Mark Price |
| > slTriggerBy | String | Stop-Loss trigger price type <br>`market`: Market Price <br>`mark`: Mark Price |
| > takeProfit | String | Take-Profit trigger price |
| > stopLoss | String | Stop-Loss trigger price |
| > tpOrderType | String | Take-Profit trigger strategy order type <br>`limit`: Limit Order <br>`market`: Market Order |
| > slOrderType | String | Stop-Loss trigger strategy order type <br>`limit`: Limit Order <br>`market`: Market Order |
| > tpLimitPrice | String | Take-Profit strategy order execution price |
| > slLimitPrice | String | Stop-Loss strategy order execution price |
| > triggerBy | String | Trigger order trigger price type <br>`market`: Market Price <br>`mark`: Mark Price |
| > triggerPrice | String | Trigger order trigger price |
| > triggerOrderType | String | Trigger order type <br>`limit`: Limit Order <br>`market`: Market Order |
| > triggerOrderPrice | String | Trigger order execution price |
| > createdTime | String | Order created timestamp (Unix milliseconds) |
| > updatedTime | String | Order update timestamp (Unix milliseconds) |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/Strategy-Order-Channel)


---

# ADL Notification Channel

### Description

Adl Notification Channel
Request Example

```
{
    "op": "subscribe",
    "args": [
        {
            "instType": "UTA",
            "topic": "adl-notification"
        }
    ]
}
```

### Request Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation <br> `subscribe`/`unsubscribe` |
| args | List<Object> | Yes | Subscribed channels |
| > instType | String | Yes | Product type<br>`UTA` Unified trading account |
| > topic | String | Yes | Topic <br> `adl-notification` ADL Notification |

Response Example

```
{
  "event": "subscribe",
  "arg": {
    "instType": "UTA",
    "topic": "adl-notification"
  },
  "connId": "xxxxxxxxxx"
}
```

### Response Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| event | String | Operation <br>`subscribe` / `unsubscribe` / `error` |
| arg | Object | Subscribed channel |
| > instType | String | Product type <br>`UTA` |
| > topic | String | Topic <br>`adl-notification` ADL Notification |
| code | String | Error code |
| msg | String | Error message |
| connId | String | Connection ID |

Push Data

```
{
  "data": [
    {
      "symbol": "BTCUSDT",
      "side": "buy",
      "status": "triggered",
      "price": "88291.2",
      "amount": "2.35",
      "ts": "1740546523244"
    }
  ],
  "arg": {
    "instType": "UTA",
    "topic": "adl-notification"
  },
  "action": "update",
  "ts": 1740546523244
}
```

### Push Parameters

| Parameters | Type | Description |
| --- | --- | --- |
| arg | Object | Subscribed channel |
| > instType | String | product type <br>`UTA` Unified trading account |
| > topic | String | Topic <br>`adl-notification` ADL Notification |
| action | String | Action <br>`snapshot` Full data <br>`update` Incremental data |
| data | List<Object> | Subscribed data |
| >symbol | String | Symbol name |
| >side | String | Position side <br>`buy`Buy<br>`sell`Sell |
| >status | String | ADL status <br>`triggered`Triggered |
| >price | String | ADL execution price |
| >amount | String | ADL execution amount <br> Unit: quote coin. |
| >ts | String | ADL start time (Unix millisecond timestamp) |

[Source](https://www.bitget.com/api-doc/uta/websocket/private/ADL-Notification-Channel)

