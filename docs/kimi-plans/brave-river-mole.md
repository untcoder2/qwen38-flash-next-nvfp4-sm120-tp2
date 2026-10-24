# X1 RX-шардинг: AEAD-дешифрование из движок-таска в reader-таски дорог

## Verdictы контрактов (полевое исследование, file:line)

1. **Приёмник на клиенте — ОДИН на сессию, не на дорогу.** `Engine.rx: TransportReceiver`
   (session.rs:5923) вскрывает кадры ВСЕХ дорог (`on_frame` session.rs:7064
   `self.rx.open(&bytes, &self.aad_in)`). Зеркало ноды — `SessionCipher.rx:
   Mutex<TransportReceiver>` (ggpowerd/session.rs:173), док «open — приёмное
   anti-replay окно bitmap на направление; дубль реплики гасится им как benign
   Replay; дедуп по (stream,seq) — работа sched §4».
2. **AEAD stateless по (seq, key).** `TransportReceiver::open` (noise transport.rs:444):
   seq — в заголовке кадра (первые 8 Б, открытый); окно — экран ДО AEAD; ключ —
   детерминированный `KeyChain.epoch_of(seq)`. Значит reader-таск дороги с
   СОБСТВЕННЫМ экземпляром приёмника (тот же k0/rekey_at/размер окна) вскрывает
   свои кадры без какого либо общения с другими дорогами. Гонок нет: один
   reader = один приёмник = in-order.
3. **Кросс-дорожные дубли после шардинга судит книга, не окно**: `on_capsule`
   (sched scheduler.rs:5687) → `seen.observe(stream, seq)` — conn-agnostic;
   `Duplicate → road_note(conn, seq, len, false)` (5720-5728, F8 una-гейт),
   `Outside → road_note_outside` (5739-5746, кумулятив не трогает), `Violation →
   on_conn_down` — **скрытого кросс-дорожного порядок-гейта нет**: атрибуция
   First уже сегодня — чья первая вскрытая копия; гонка «кто первый» просто
   переносится из handle-порядка в read-порядок. on_ack идемпотентен к дублю
   Ack-капсулы (una clamp 5115+, inflight-remove монотонен, Karn-гейт RTT-сэмпла
   только при релизе). → док-вердикт + клетка `x1_crossroad_dup_order_gate_none`.
4. **Мина, которую обязан закрыть срез: холодное окно re-dial'ной дороги.** Новая
   жизнь дороги прилетает на сессионный фронтир seq≈N; её приёмник с `next=0`
   дал бы PERMANENT FutureWindow (commit не двигает next без успеха → стена
   benign-смерти). Лечение: общий водяной знак `rx_wmark: Arc<AtomicU64>` —
   `fetch_max(seq+1)` на каждом успешном открытии (reader или raw-путь), fork
   новой жизни сидируется `seed_next = wmark.load()`.
5. **Base-побайтовость не тронута**: wire/TX не двигается (seal остаётся в движке);
   ChaCha переезжает в reader — runtime, не байты. `Frame/Frames` — живые
   fallback-варианты (сырой reader-путь + все тест-инжекты), handle() принимает
   ОБА пути; клетка-аудит фиксирует боевой дефолт = шардинг.
6. `attach_conn` уже несёт стендовый рубильник-паттерн (`coalesce`/`write_stall`:
   None = боевой резолв, Some = явно) — `rx_shard: Option<bool>` в том же рецепте,
   env НЕ пачкается.

## Правки

### A. `ggpower/crates/ggpower-noise/src/transport.rs` (только добавления)
- `ReplayWindow::with_capsules_seeded(window_capsules, seed_next)` — total: seed
  клампится к `MAX_SEQ+1`-границе, bitmap нули.
- `TransportReceiver::fork_road_rx(&self, seed_next: u64) -> Self` — `KeyChain::
  with_floor(chain.k0, chain.rekey_at, window.size())` + сидированное окно.
  Ни существующий API, ни байты seal/open не меняются.
- Клетки noise: fork вскрывает кадры sender'а на frontier-риддере; bitmap fork'а
  пуст (источник не «делится» коммитами — кросс-дорожный дубль вскрывается, книга
  дедупит); FutureWindow только за seed+окном; seed=u64::MAX total (паник нет);
  armed-размер окна переживает fork; мутационные пины (fork, поделивший bits →
  красный).

### B. `ggpower/crates/ggpower-client/src/session.rs`
1. `DecItem { opened: Result<Vec<u8>, NoiseError>, frame_len: usize }` (pub(crate))
   + `EngineMsg::Decrypted { conn, gen, items: Vec<DecItem> }` (док: semantics
   «последовательность тех же on_frame-исходов того же reader'а»; singleton =
   один item, батч = один канал-оп, как F3 Frames).
2. `Engine.rx_wmark: Arc<AtomicU64>` — поле + все literals (боевой ~21471,
   стендовые 10834/15609/18151/18305).
3. `on_frame` распадается на `on_frame` (raw: rx.open → wmark-флаг → два хелпера),
   `on_frame_opened(conn, frame_len, pt)` (беняки: benign=0, note_road_opened,
   rx_last_at, wcap::decode, junk-ветка, frames_rx, dispatch_capsule) и
   `on_frame_open_err(conn, frame_len, e)` (benign/note_road_activity, stale
   storm, OPEN FAIL печать по frame_len, CounterExhausted→on_conn_down, junk
   storm). Books остаются ЕДИСТВЕННЫми — handle-ветки Frame/Frames/Decrypted
   сходятся в эти же два хелпера (equivalence по построению).
4. `handle()` — `EngineMsg::Decrypted`: per-item gen-страж (`break` как у Frames) +
   match item → хелперы.
5. `attach_conn(..., rx_shard: Option<bool>)`: None = бой (шардинг ON). Reader
   при ON: `let mut rx = self.rx.fork_road_rx(self.rx_wmark.load(Relaxed))`,
   клон `aad_in`/`rx_wmark` в таск; per-frame `rx.open` → `Ok(op)`: wmark
   fetch_max(op.seq.saturating_add(1)) + item Ok(payload)/frame_len; `Err(e)`:
   item Err(e); один `Decrypted` на read-проход (singleton включительно).
   При Some(false) — дословный прежний сырой путь (Frame/Frames), fork не
   строится (нулевая стоимость).
6. Call-sites: бой (`on_member_up`:7014, establish-уолл :21553) — None;
   сырые ScriptedReader-пины :20989/:21017 — Some(false) (fallback жив);
   sealed-риги (r164 :11451/:11643, F3-бенч :20387, EgressProbe :15674,
   idle/parked/w13) — None = боевой шардинг.
7. Клетки (боевой движок, in-proc duplex; мутационные пины в доках):
   - `x1_reader_decrypted_path_matches_handle_books` — ЭКВИВАЛЕНТНОСТЬ: depth-N
     sealed-поток через reader(ON) vs те же кадры через handle(Frame) —
     инбоксы, паттерн-целостность, rx_delivered, frames_rx совпадают.
   - `x1_reader_batches_one_msg_per_pass` — структура Decrypted (порядок/полнота).
   - `x1_stale_gen_decrypted_dropped` — re-dial гонка: события замещённой жизни
     (старый gen) — тихий дроп, книги целы.
   - `x1_decryptfailed_verdict_books` — подделка через reader: rx_anomaly_seen,
     junk до профильного потолка → смерть дороги; Err(CounterExhausted)-вердикт
     → немедленный on_conn_down (клетка через прямой Decrypted-инжект).
   - `x1_frontier_seed_survives_redial` — вторая жизнь на seq ≫ окна: фронтир
     вскрывается, FutureWindow-шторма нет (убийца «холодного окна»).
   - `x1_battle_default_shards_raw_path_is_fallback` — аудит: None ⇒ из reader
     только Decrypted (ни одного Frame/Frames), Some(false) ⇒ сырой путь жив.
   - `x1_crossroad_dup_order_gate_none` — байт-идентичная реплика на двух
     дорогах: одно приложение-доставление, rx_delivered не отравлен дублем,
     порядок A-first/B-first даёт равные deliveries.
8. Док-синхрон: шапка модуля :6, доки EngineMsg::Frame/Frames (fallback-статус),
   attach_conn, F3-стенды («у движка ЕДИНСТВЕННЫЙ rx» → per-road fork + wmark),
   r164 «читатель... rx.open» — читательской дешифровкой.

### C. Вне владения (не трогать)
`ggpowerd/**`, `ggpower-sched/src/**`, тесты `w500_accept**`, peer.rs (вердикт:
ServerEngine — нодовый симулятор, станционный downlink-контур его reader'а не
тронут; `SrvMsg::Frame`-ген-страж использован образцом), `wireio.rs` (входной
тип не нужен: Decrypted несёт post-AEAD payload, `wcap::decode` не двигается).

## Гейты (из ggpower/, cargo +stable-x86_64-pc-windows-msvc, терпеть cargo-локи)
1. `cargo test -p ggpower-noise --lib` (89 + новые)
2. `cargo test -p ggpower-client --lib` (база 472/0 + новые)
3. `cargo test -p ggpower-client --bins`
4. интеграционные: `--test interop --test fleet_e2e_429 --test livelock_repro --test
   close_path_259 --test n3_transport_base_glue --test n3_transport_armed_glue --test
   w500_accept_base_8192 --test w500_accept_armed_32768`
5. `--release` прогон client --lib (F3 ≥60 МБ/с контракт) + noise release
6. Запись в `docs/notes.md`; НЕ коммитить/не пушить (родитель).
