# btc-race-24h-20261118-1444

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/yowanda/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/yowanda/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Long-only CBBTC/USDC bot for make.fun's 24-hour BTC race (v6): oversold-bounce entry — arm when the close is at least 2% below the highest close of the last 240 bars, enter on a close above the 60-bar Donchian high — with a Chandelier trailing exit (sell 3x ATR(60) below the highest close since entry). No trend filter, no take-profit: the entry is justified by the washout itself, and the exit rides the bounce until volatility says it has reversed. v6 is the only version of this bot family that has ever passed the dev gate (+$14.35 net PnL, race 20261115-0828). Pure integer math, deterministic, stdlib only, speaks agent-fun/bars-replay/v1 via the vendored SDK shim.

## Run it

```sh
python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=vpa00rt3&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
