# 生产机部署 step-by-step（issue #32）

> spec: `openspec/specs/runtime-isolation/spec.md` + `openspec/specs/delivery-roadmap/spec.md`
>
> 目标: 新生产机按本文档操作 30 分钟内跑通 `copy-trader doctor` 与第一个 LiveRunner 实例。

## 前置条件

- Linux 主机(Debian 12 / Ubuntu 22.04+ / CentOS 9+)
- root 权限或 sudo
- 网络出站到 `api.binance.com`(若用 Binance) / `api.hyperliquid.xyz`(若用 Hyperliquid)
- 至少 4GB RAM / 20GB 磁盘 / Python 3.12+

## 1. 创建专用 user 与目录

```bash
sudo useradd -r -m -d /var/lib/copy_trader -s /bin/bash copy_trader
sudo mkdir -p /var/lib/copy_trader/{state,logs,pids,db,secrets}
sudo mkdir -p /var/log/copy_trader /etc/copy_trader
sudo chown -R copy_trader:copy_trader /var/lib/copy_trader /var/log/copy_trader
sudo chmod 0700 /var/lib/copy_trader/{state,logs,pids,db,secrets}
sudo chmod 0700 /etc/copy_trader
```

## 2. 安装 uv

```bash
sudo -u copy_trader bash -c '
curl -LsSf https://astral.sh/uv/install.sh | sh
echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> ~/.bashrc
'

# 链接到 /usr/local/bin 让 systemd 找得到
sudo ln -sf /var/lib/copy_trader/.local/bin/uv /usr/local/bin/uv
```

## 3. clone 仓库

```bash
sudo -u copy_trader bash -c '
cd /var/lib/copy_trader
git clone https://github.com/lizhao903/copy_trader.git src
cd src
uv sync --frozen
'

# 链接 copy-trader 命令到 /usr/local/bin
sudo ln -sf /var/lib/copy_trader/src/.venv/bin/copy-trader /usr/local/bin/copy-trader
```

## 4. 配置 secrets.env

```bash
sudo bash -c 'cat > /etc/copy_trader/secrets.env <<EOF
# 凭证;权限 0600,只 copy_trader 用户可读
# 命名遵循 *_KEY / *_SECRET / *_TOKEN / *_PRIVATE_KEY 后缀;不能在 yaml 出现

# Binance spot (如启用)
BINANCE_API_KEY=<填真实 key>
BINANCE_API_SECRET=<填真实 secret>

# Hyperliquid spot (如启用)
HYPERLIQUID_PRIVATE_KEY=<填 0x... 64 字符>

# 其他需要走 envvar 注入的字段
EOF'

sudo chmod 0600 /etc/copy_trader/secrets.env
sudo chown root:root /etc/copy_trader/secrets.env  # systemd 启动时读
```

## 5. 安装 systemd unit

```bash
sudo cp /var/lib/copy_trader/src/deploy/systemd/copy-trader@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 6. 跑 doctor 自检(必须退出码 0)

```bash
sudo -u copy_trader bash -c '
export COPY_TRADER_ENV=prod
export COPY_TRADER_HOME=/var/lib/copy_trader
copy-trader doctor
'
```

期望输出含:

- `[runtime] home=/var/lib/copy_trader env_tag=prod machine_id=<UUID>`
- `[subdirs] state OK / logs OK / pids OK / db OK / secrets OK`
- `[ledger] schema_version=<not yet implemented>` 或 `2`(若已建 ledger)
- `[config sources]` 各字段 LayerScope
- 敏感字段全 `<redacted>`
- **没有任何 ⚠️ warning**(如有,排查锁文件 / env 配置)

## 7. 配置 yaml + 启动第一个 LiveRunner

config 在仓库 `config/{base,prod}.yaml`。生产 override 写 `/var/lib/copy_trader/config.yaml`(可选 local layer):

```bash
sudo -u copy_trader bash -c '
cat > /var/lib/copy_trader/config.yaml <<EOF
accounts:
  spot:
    venue: binance.spot
    enabled: true
    credentials_alias: BINANCE
    symbols: [BTCUSDT, ETHUSDT]

capital_allocation:
  - account: spot
    strategy: hello
    quote_asset: USDT
    max_quote_amount: 100.0
    reserve_quote_amount: 20.0

# pyramid / fixed_position 二选一(同 account+strategy 互斥)
fixed_position:
  - account: spot
    strategy: hello
    mode: fixed_qty
    qty: 0.001
    max_price: 60000
EOF'
```

启动第一个 LiveRunner 实例(命名 `<strategy>-<account>`):

```bash
sudo systemctl enable copy-trader@hello-spot
sudo systemctl start copy-trader@hello-spot
sudo systemctl status copy-trader@hello-spot
sudo journalctl -u copy-trader@hello-spot -f
```

## 8. 看 Dashboard(m4 实装后)

```bash
# Dashboard 默认监听 127.0.0.1:8080(m4 #28 实装)
curl http://127.0.0.1:8080/overview
```

## Dry-run checklist 模板

每次新生产机部署或换 milestone 时跑一遍:

- [ ] user copy_trader 已创建,各子目录权限 0700
- [ ] uv 在 PATH,版本 ≥ 0.7
- [ ] git clone 成功,`uv sync --frozen` 跑过
- [ ] /usr/local/bin/copy-trader 软链接存在
- [ ] /etc/copy_trader/secrets.env 权限 0600,凭证已填
- [ ] systemd unit 已 cp 到 /etc/systemd/system/,daemon-reload 跑过
- [ ] `COPY_TRADER_ENV=prod copy-trader doctor` 退出码 0,无 ⚠️
- [ ] /var/lib/copy_trader/config.yaml 配置 accounts/capital/(pyramid|fixed_position)
- [ ] 至少一个 `copy-trader@<strategy>-<account>` 已 enable + start,journal 看到主循环 tick
- [ ] **首次启动后 24h 跑 [docs/CANARY_CHECKLIST.md](CANARY_CHECKLIST.md) day-1 检查**(reconcile diff / critical alerts / PnL 偏差三类指标)

## 故障排查速查

| 症状 | 排查 |
|------|------|
| `MissingEnvError: COPY_TRADER_ENV` | systemd unit 已设;手工 export 时记得设 prod |
| doctor 报 ⚠️ env_tag mismatch | `/var/lib/copy_trader/state/.runtime_lock.json` 是上次部署留的;若确认是同台机器同环境,删除锁文件后重跑 doctor |
| `CrossEnvironmentWriteError` | 同一 ledger 被 dev/paper/prod 共享;按 spec 应该一机一 env,删 ledger 或换 home 目录 |
| systemd `failed (Result: exit-code)` | `journalctl -u copy-trader@... -n 100`;常见: secrets.env 权限错 / venue 凭证无效 / config.yaml schema 校验 fail |
| `unknown_position_on_exchange` 拒启动 | 跑一次 `copy-trader reconcile --account spot --acknowledge-unknown` 接受现状 |

## 升级 / 回滚

新版本部署: `sudo bin/deploy.sh bootstrap/m<N>-<YYYYMMDD>` (issue #30)

回滚: `sudo bin/rollback.sh` (回上一个 tag) 或 `sudo bin/rollback.sh bootstrap/m<N>-<old-date>` (显式指定)。

详见 [docs/CONTRIBUTING.md](CONTRIBUTING.md) 灰度门槛章节 + [docs/CANARY_CHECKLIST.md](CANARY_CHECKLIST.md)。
