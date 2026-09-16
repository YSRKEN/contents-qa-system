"""コマンドラインインターフェース。

  cqs works                        作品一覧
  cqs new "作品名"                 作品DBを作る
  cqs -w <slug> ...                作品を指定して操作する
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import answer as answer_mod
from . import config, ingest
from .constants import SOURCE_KINDS, VERIFICATIONS, kind_label
from .store import StoreError, WorkStore


def _out(args: argparse.Namespace, payload: Any, human: str | None = None) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif human is not None:
        print(human)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _store(args: argparse.Namespace) -> WorkStore:
    if not args.work:
        raise SystemExit("作品を -w/--work で指定してください（一覧: cqs works）")
    return config.open_work(args.work)


def _robots_setting(args: argparse.Namespace) -> bool | None:
    """robots.txt を確認するかどうか。指定が無ければ環境変数の既定に委ねる。"""
    if getattr(args, "robots", False):
        return True
    if getattr(args, "no_robots", False):
        return False
    return None


def _read_input(path: str | None, text: str | None) -> str:
    if text:
        return text
    if path == "-" or path is None:
        data = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as f:
            data = f.read()
    if not data.strip():
        raise SystemExit("入力が空です")
    return data


# --- 各コマンド --------------------------------------------------------------


def cmd_works(args: argparse.Namespace) -> None:
    works = config.list_works()
    if args.json:
        _out(args, works)
        return
    if not works:
        print(f"作品DBがありません（{config.data_dir()}）。 cqs new \"作品名\" で作成してください。")
        return
    print(f"{config.data_dir()}")
    for w in works:
        if w.get("error"):
            print(f"  {w['file_slug']:<24} 読み込み失敗: {w['error']}")
            continue
        print(
            f"  {w['file_slug']:<24} {w['title']}  "
            f"出典{w['sources']}件 / 版{w['versions']}件 / 主張{w['claims_active']}件 / "
            f"エンティティ{w['entities']}件 / 未照合候補{w['candidates_pending']}件"
        )


def cmd_new(args: argparse.Namespace) -> None:
    st = config.create_work(args.title, slug=args.slug, note=args.note or "")
    _out(
        args,
        {"path": str(st.path), **st.meta},
        f"作成しました: {st.path}\n  slug={st.meta['slug']} title={st.meta['title']}",
    )
    st.close()


def cmd_stats(args: argparse.Namespace) -> None:
    with _store(args) as st:
        s = st.stats()
        if args.json:
            _out(args, s)
            return
        print(f"{s['title']} ({s['slug']})  {st.path}")
        print(f"  出典 {s['sources']} / 版 {s['versions']} / 主張 {s['claims_active']}（全{s['claims_total']}）")
        print(f"  エンティティ {s['entities']} / 未照合候補 {s['candidates_pending']}")
        if s["sources_by_kind"]:
            print("  出典種別: " + ", ".join(f"{kind_label(k)} {v}" for k, v in s["sources_by_kind"].items()))
        if s["claims_by_verification"]:
            print("  確認状態: " + ", ".join(f"{VERIFICATIONS.get(k, k)} {v}" for k, v in s["claims_by_verification"].items()))


def cmd_fetch(args: argparse.Namespace) -> None:
    with _store(args) as st:
        results = []
        for url in args.urls:
            try:
                r = ingest.ingest_url(
                    st, url, kind=args.kind, title=args.title,
                    respect_robots=_robots_setting(args),
                )
            except Exception as e:
                r = {"url": url, "error": str(e)}
            results.append(r)
        if args.json:
            _out(args, results)
            return
        for r in results:
            if "error" in r:
                print(f"× {r['url']}: {r['error']}")
            else:
                mark = "新しい版" if r["changed"] else "変化なし"
                print(
                    f"○ [{r['kind_label']}] {r['title'] or ''} v{r['version_no']} ({mark}) "
                    f"{r['text_length']}字  source_version={r['source_version_id']}  {r['url']}"
                )
                if r.get("rechecked_claims"):
                    print(f"    本文が変化したため {r['rechecked_claims']} 件の主張を要再確認にしました")


def cmd_refetch(args: argparse.Namespace) -> None:
    with _store(args) as st:
        results = ingest.refetch_sources(st, only_periodic=not args.all)
        changed = [r for r in results if r.get("changed")]
        if args.json:
            _out(args, results)
            return
        for r in results:
            if "error" in r:
                print(f"× {r['url']}: {r['error']}")
        print(f"{len(results)}件を再取得し、{len(changed)}件で本文が変化しました。")
        for r in changed:
            print(f"  更新: {r['url']} → v{r['version_no']} (要再確認 {r.get('rechecked_claims', 0)}件)")


def cmd_add_text(args: argparse.Namespace) -> None:
    text = _read_input(args.file, args.text)
    with _store(args) as st:
        r = ingest.ingest_text(st, text, kind=args.kind, title=args.title, url=args.url, note=args.note)
        _out(args, r, f"取り込みました: source_version={r['source_version_id']} ({r['text_length']}字)")


def cmd_add_report(args: argparse.Namespace) -> None:
    text = _read_input(args.file, args.text)
    with _store(args) as st:
        r = ingest.ingest_ai_report(st, text, title=args.title, note=args.note)
        _out(
            args, r,
            f"調査報告を取り込みました: source_version={r['source_version_id']}\n"
            f"  候補 {r['candidates']} 件（うちURL付き {r['with_url']} 件）\n"
            f"  照合するには: cqs -w {args.work} verify",
        )


def cmd_candidates(args: argparse.Namespace) -> None:
    with _store(args) as st:
        rows = st.list_candidates(status=None if args.status == "all" else args.status, limit=args.limit)
        if args.json:
            _out(args, rows)
            return
        for c in rows:
            print(f"[{c['id']}] ({c['status']}) {c['claim_text']}")
            print(f"     URL: {c['url'] or '（なし）'}  {c['note'] or ''}")
        print(f"{len(rows)}件")


def cmd_verify(args: argparse.Namespace) -> None:
    with _store(args) as st:
        rows = ingest.verify_candidates(
            st,
            candidate_ids=args.id or None,
            limit=args.limit,
            promote_threshold=args.promote,
            respect_robots=_robots_setting(args),
        )
        if args.json:
            _out(args, rows)
            return
        for r in rows:
            if "error" in r:
                print(f"× 候補{r['candidate_id']}: {r['error']}")
                continue
            mark = "→ claim %s として登録" % r["claim_id"] if r.get("promoted") else "（未登録）"
            print(f"候補{r['candidate_id']} 一致 {r['score']:.0%} {mark}")
            print(f"     {r['claim_text']}")
            print(f"     {r['url']} [{r['kind_label']}] source_version={r['source_version_id']}")
            if r["missing"]:
                print(f"     ページに無い語: {', '.join(r['missing'][:8])}")


def cmd_sources(args: argparse.Namespace) -> None:
    with _store(args) as st:
        rows = st.list_sources(kind=args.kind)
        if args.json:
            _out(args, rows)
            return
        for s in rows:
            print(f"[{s['id']}] {s['kind_label']:<16} v{s['versions']} {s['title'] or ''}")
            print(f"      {s['url'] or '（URLなし）'}  最終取得 {s['last_fetched'] or '-'}")
        print(f"{len(rows)}件")


def cmd_images(args: argparse.Namespace) -> None:
    from . import images

    with _store(args) as st:
        if args.images_command == "list":
            rows = images.list_images(st, args.source_version_id, include_chrome=args.all)
            if args.json:
                _out(args, [r.__dict__ for r in rows])
                return
            for r in rows:
                size = f"{r.width}x{r.height}" if r.width and r.height else ""
                print(f"  {size:<10} {r.alt[:28]:<30} {r.url[:96]}")
            print(f"{len(rows)}件")
        elif args.images_command == "save":
            rows = images.save_all(st, args.source_version_id, args.out)
            if args.json:
                _out(args, rows)
                return
            for r in rows:
                print(f"× {r['url']}: {r['error']}" if "error" in r
                      else f"○ {r['path']}  ({r['bytes']//1024}KB)  {r['url'][:70]}")
            print(f"{args.out} に保存しました。読み取り結果は "
                  f"cqs -w {args.work} add-text --kind image_transcript --url <画像URL> で入れられます。")
        elif args.images_command == "transcribe":
            rows = images.transcribe(st, args.source_version_id, model=args.model, limit=args.limit)
            if args.json:
                _out(args, rows)
                return
            for r in rows:
                print(f"× {r['url']}: {r['error']}" if "error" in r
                      else f"○ {r['chars']:>5}字 → source_version {r['source_version_id']}  {r['url'][:70]}")


def cmd_rederive(args: argparse.Namespace) -> None:
    with _store(args) as st:
        if args.source_version_id:
            targets = [args.source_version_id]
        else:
            targets = [int(r["id"]) for r in st.conn.execute(
                "SELECT id FROM source_versions WHERE raw_gz IS NOT NULL ORDER BY id")]
        results = [st.rederive_text(v) for v in targets]
        changed = [r for r in results if r.get("changed")]
        if args.json:
            _out(args, results)
            return
        for r in changed:
            print(f"  v{r['version_id']}: {r['before']}字 → {r['after']}字")
        print(f"{len(results)}件を作り直し、{len(changed)}件で本文が変わりました。")
        if changed:
            print("  変わった版の主張は取り込み直してください（cqs propose <id> --register）")


def cmd_source_kind(args: argparse.Namespace) -> None:
    with _store(args) as st:
        st.set_source_kind(args.source_id, args.kind)
        _out(args, {"ok": True}, f"出典 {args.source_id} の種別を {kind_label(args.kind)} にしました")


def cmd_wiki(args: argparse.Namespace) -> None:
    from . import wiki

    with _store(args) as st:
        if args.wiki_command == "refs":
            raw = st.get_raw_html(args.source_version_id)
            if not raw:
                raise SystemExit("生HTMLが保存されていません")
            pending = set(wiki.pending_reference_urls(st, args.source_version_id))
            urls = wiki.reference_urls(raw)
            if args.json:
                _out(args, {"urls": urls, "pending": sorted(pending)})
                return
            for u in urls:
                print(("  " if u in pending else "○ ") + u)
            print(f"{len(urls)}件（うち未取得 {len(pending)}件）")
        elif args.wiki_command == "fetch-refs":
            rows = wiki.fetch_references(st, args.source_version_id, limit=args.limit)
            if args.json:
                _out(args, rows)
                return
            for r in rows:
                if "error" in r:
                    print(f"× {r['url']}: {r['error']}")
                else:
                    print(f"○ [{r['kind_label']}] {r['text_length']:>6}字 {r['url']}")
        elif args.wiki_command == "annotate":
            r = wiki.annotate_claims(st, args.source_version_id)
            _out(
                args, r,
                f"{r.get('annotated', 0)} 件の主張に脚注を書き添えました"
                f"（脚注 {r.get('citations', 0)} 件 / URL付き参照 {r.get('markers_with_url', 0)}"
                f" / 書籍のみ {r.get('markers_book_only', 0)}）",
            )


def cmd_search(args: argparse.Namespace) -> None:
    with _store(args) as st:
        rows = st.search_sources(args.query, kind=args.kind, limit=args.limit)
        if args.json:
            _out(args, rows)
            return
        for r in rows:
            print(f"[source_version {r['source_version_id']}] {r['kind_label']} {r.get('source_title') or ''}")
            print(f"      {r.get('url') or ''}")
            print(f"      …{r['excerpt']}…")
        print(f"{len(rows)}件")


def cmd_show(args: argparse.Namespace) -> None:
    with _store(args) as st:
        v = st.source_excerpt(args.source_version_id, offset=args.offset, length=args.length)
        if not v:
            raise SystemExit(f"見つかりません: {args.source_version_id}")
        if args.json:
            _out(args, v)
            return
        print(f"{v['source_title'] or v['title'] or ''} [{v['kind_label']}] v{v['version_no']}")
        print(f"{v['url'] or ''}  取得 {v['fetched_at']}  全{v['total_length']}字")
        print("-" * 60)
        print(v["excerpt"])
        if v["has_more"]:
            print(f"… （続きは --offset {args.offset + args.length}）")


def cmd_claims(args: argparse.Namespace) -> None:
    with _store(args) as st:
        rows = st.search_claims(
            query=args.query, entity=args.entity, kind=args.kind,
            verification=args.verification, status=None if args.all else "active", limit=args.limit,
        )
        if args.json:
            _out(args, rows)
            return
        for c in rows:
            print(f"[{c['id']}] {c['text']}")
            print(
                f"      {c['verification_label']} / {c['kind_label']} / "
                f"{c.get('url') or c.get('source_title') or '出典なし'}"
                + (f" / 対象: {', '.join(c['entities'])}" if c["entities"] else "")
            )
        print(f"{len(rows)}件")


def cmd_claim_add(args: argparse.Namespace) -> None:
    with _store(args) as st:
        cid = st.add_claim(
            text=args.text, source_version_id=args.source_version,
            verification=args.verification, entities=args.entity or (),
            locator=args.locator, note=args.note, supersedes=args.supersedes,
        )
        _out(args, {"claim_id": cid}, f"登録しました: claim {cid}")


def cmd_claim_link(args: argparse.Namespace) -> None:
    with _store(args) as st:
        st.link_claims(args.from_id, args.to_id, args.type, note=args.note)
        _out(args, {"ok": True}, f"claim {args.from_id} -{args.type}-> {args.to_id}")


def cmd_claim_verify(args: argparse.Namespace) -> None:
    with _store(args) as st:
        st.set_verification(args.claim_id, args.verification, note=args.note)
        _out(args, {"ok": True}, f"claim {args.claim_id} の確認状態を {args.verification} にしました")


def cmd_propose(args: argparse.Namespace) -> None:
    with _store(args) as st:
        if args.register:
            # 指定が無ければ出典種別に任せる（参照系の出典は人物名の無い文も採る）。
            # ここで常に True を渡すと、Wikiや公式サイトから用語・設定の説明が
            # 丸ごと落ちる（--allow-no-entity を毎回付けないと拾えなくなる）。
            require = False if args.allow_no_entity else (True if args.require_entity else None)
            ids = ingest.register_proposed(
                st, args.source_version_id, entities=args.entity or (), limit=args.limit,
                require_entity=require,
            )
            _out(args, {"claim_ids": ids}, f"{len(ids)}件を主張として登録しました: {ids[:10]}{'…' if len(ids) > 10 else ''}")
            return
        rows = ingest.propose_claims(st, args.source_version_id, entities=args.entity or (), limit=args.limit)
        if args.json:
            _out(args, rows)
            return
        for i, c in enumerate(rows, 1):
            ents = f"  [{', '.join(c['entities'])}]" if c["entities"] else ""
            print(f"{i:3}. {c['text']}{ents}")
        print(f"{len(rows)}件（登録は cqs -w {args.work} claim add --text ... --source-version {args.source_version_id}）")


def cmd_entity(args: argparse.Namespace) -> None:
    with _store(args) as st:
        if args.entity_command == "add":
            eid = st.ensure_entity(args.name, kind=args.kind, aliases=args.alias or (), note=args.note)
            _out(args, {"entity_id": eid}, f"登録しました: {args.name} (id={eid})")
        elif args.entity_command == "list":
            rows = st.list_entities()
            if args.json:
                _out(args, rows)
                return
            for e in rows:
                al = f"  別名: {', '.join(e['aliases'])}" if e["aliases"] else ""
                print(f"[{e['id']}] {e['name']}  ({e['kind'] or '-'})  主張{e['claims']}件{al}")
            print(f"{len(rows)}件")
        elif args.entity_command == "coverage":
            rows = st.entity_coverage()
            if args.kind:
                rows = [r for r in rows if r["kind"] == args.kind]
            if args.json:
                _out(args, rows)
                return
            print(f"{'名前':<16}{'主張':>5}{'出典':>5}{'公式':>5}{'記事':>5}{'伝聞':>5}{'感想':>5}")
            for r in rows:
                print(f"{r['name']:<16}{r['claims'] or 0:>5}{r['sources'] or 0:>5}"
                      f"{r['official'] or 0:>5}{r['article'] or 0:>5}"
                      f"{r['secondhand'] or 0:>5}{r['fan'] or 0:>5}")
            thin = [r for r in rows if (r["sources"] or 0) <= 2]
            if thin:
                print(f"\n出典が2つ以下のエンティティ: {', '.join(r['name'] for r in thin)}")
        elif args.entity_command == "related":
            rows = st.related_entities(args.name)
            if args.json:
                _out(args, rows)
                return
            for r in rows:
                print(f"{r['name']}  同じ主張に {r['shared']} 件")


def cmd_source_extract(args: argparse.Namespace) -> None:
    with _store(args) as st:
        mode = None if args.mode == "auto" else args.mode
        st.set_source_extract(args.source_id, mode)
        _out(args, {"ok": True}, f"出典 {args.source_id} の取り込み方を {args.mode} にしました")


def cmd_segment(args: argparse.Namespace) -> None:
    with _store(args) as st:
        if args.segment_command == "rule":
            if args.pattern is None:
                rules = st.segment_rules()
                _out(args, rules, "\n".join(f"{p} → {seg}" for p, seg in rules) or "（ルールなし）")
                return
            st.set_segment_rule(args.pattern, args.segment)
            n = st.apply_segment_rules()
            _out(args, {"ok": True, "applied": n},
                 f"ルールを追加しました: {args.pattern} → {args.segment}（既存 {n} 件に反映）")
        elif args.segment_command == "set":
            st.set_source_segment(args.source_id, args.segment or None)
            _out(args, {"ok": True}, f"出典 {args.source_id} の区分を {args.segment or '（なし）'} にしました")
        elif args.segment_command == "apply":
            n = st.apply_segment_rules()
            _out(args, {"applied": n}, f"{n}件の出典に区分を付けました")
        else:
            rows = [dict(r) for r in st.conn.execute(
                "SELECT COALESCE(segment, '（未分類）') AS segment, COUNT(*) AS sources "
                "FROM sources GROUP BY 1 ORDER BY sources DESC")]
            _out(args, rows, "\n".join(f"{r['segment']}  出典{r['sources']}件" for r in rows) or "（出典なし）")


def cmd_rule(args: argparse.Namespace) -> None:
    with _store(args) as st:
        if args.pattern is None:
            _out(args, st.kind_rules(), "\n".join(f"{p} → {kind_label(k)}" for p, k in st.kind_rules()) or "（ルールなし）")
            return
        st.set_kind_rule(args.pattern, args.kind)
        _out(args, {"ok": True}, f"ルールを追加しました: {args.pattern} → {kind_label(args.kind)}")


def cmd_ask(args: argparse.Namespace) -> None:
    with _store(args) as st:
        r = answer_mod.answer(st, args.question, model=args.model, use_llm=not args.no_llm,
                              plan=not args.no_plan)
        if args.json:
            _out(args, r)
            return
        ctx = r["context"]
        if ctx.get("targets"):
            got = ctx.get("claims_per_target") or {}
            print("対象ごとに分けて調べました: "
                  + "、".join(f"{t}（{got.get(t, 0)}件）" for t in ctx["targets"]) + "\n")
        if r["answer"]:
            print(r["answer"])
            if r.get("follow_ups"):
                print("\n次に訊けること:")
                for q in r["follow_ups"]:
                    print(f"  - {q}")
            print(f"\n--- {r['model']} / 主張{len(r['context']['claims'])}件を参照")
        else:
            print(r["prompt"]["user"])
            print("\n" + "=" * 60)
            print(r.get("reason", ""))


def cmd_serve(args: argparse.Namespace) -> None:
    from .web.app import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


# --- パーサ ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cqs", description="作品QAシステム")
    p.add_argument("-w", "--work", help="作品スラッグ（data/<slug>.db）")
    p.add_argument("--json", action="store_true", help="JSONで出力する")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("works", help="作品一覧").set_defaults(func=cmd_works)

    sp = sub.add_parser("new", help="作品DBを作る")
    sp.add_argument("title")
    sp.add_argument("--slug")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_new)

    sub.add_parser("stats", help="作品の統計").set_defaults(func=cmd_stats)

    sp = sub.add_parser("fetch", help="URLを取得して原文層に入れる")
    sp.add_argument("urls", nargs="+")
    sp.add_argument("--kind", choices=sorted(SOURCE_KINDS))
    sp.add_argument("--title")
    sp.add_argument("--no-robots", action="store_true", help="robots.txt を確認しない（既定）")
    sp.add_argument("--robots", action="store_true", help="robots.txt を確認し、拒否されたURLは取得しない")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("refetch", help="登録済みURLを再取得して版を積む")
    sp.add_argument("--all", action="store_true", help="1回取得で確定の出典も対象にする")
    sp.set_defaults(func=cmd_refetch)

    sp = sub.add_parser("add-text", help="資料を直接入力する（検索を伴わない）")
    sp.add_argument("--title", required=True)
    sp.add_argument("--kind", default="manual", choices=sorted(SOURCE_KINDS))
    sp.add_argument("--url")
    sp.add_argument("--note")
    sp.add_argument("--file", help="ファイルパス（省略/'-' で標準入力）")
    sp.add_argument("--text")
    sp.set_defaults(func=cmd_add_text)

    sp = sub.add_parser("add-report", help="他AIの調査結果を取り込み、候補を作る")
    sp.add_argument("--title", required=True)
    sp.add_argument("--note")
    sp.add_argument("--file")
    sp.add_argument("--text")
    sp.set_defaults(func=cmd_add_report)

    sp = sub.add_parser("candidates", help="未照合の候補一覧")
    sp.add_argument("--status", default="pending", choices=["pending", "verified", "rejected", "all"])
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_candidates)

    sp = sub.add_parser("verify", help="候補のURLを取得して主張の実在を照合する")
    sp.add_argument("--id", type=int, action="append")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--promote", type=float, metavar="0.0-1.0", help="この一致率以上を知識層へ登録する")
    sp.add_argument("--no-robots", action="store_true")
    sp.add_argument("--robots", action="store_true", help="robots.txt を確認する")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("sources", help="出典一覧")
    sp.add_argument("--kind", choices=sorted(SOURCE_KINDS))
    sp.set_defaults(func=cmd_sources)

    wp = sub.add_parser("wiki", help="Wikiの脚注を扱う（原資料を辿る／主張に脚注を添える）")
    wsub = wp.add_subparsers(dest="wiki_command", required=True)
    w1 = wsub.add_parser("refs", help="脚注の外部URLを一覧する（○=取り込み済み）")
    w1.add_argument("source_version_id", type=int)
    w1.set_defaults(func=cmd_wiki)
    w2 = wsub.add_parser("fetch-refs", help="未取得の脚注リンク先を原資料として取り込む")
    w2.add_argument("source_version_id", type=int)
    w2.add_argument("--limit", type=int, default=20)
    w2.set_defaults(func=cmd_wiki)
    w3 = wsub.add_parser("annotate", help="Wiki由来の主張に、根拠となる脚注を書き添える")
    w3.add_argument("source_version_id", type=int)
    w3.set_defaults(func=cmd_wiki)

    ip = sub.add_parser("images", help="記事に貼られた画像（表・カレンダー・図）の中身を扱う")
    isub = ip.add_subparsers(dest="images_command", required=True)
    i1 = isub.add_parser("list", help="本文に属する画像を一覧する")
    i1.add_argument("source_version_id", type=int)
    i1.add_argument("--all", action="store_true", help="アイコンやサムネイルも含める")
    i1.set_defaults(func=cmd_images)
    i2 = isub.add_parser("save", help="画像を保存する（自分で読む／別の道具に渡す）")
    i2.add_argument("source_version_id", type=int)
    i2.add_argument("--out", default="./images")
    i2.set_defaults(func=cmd_images)
    i3 = isub.add_parser("transcribe", help="画像をClaudeに読み取らせ、原文層に入れる（APIキーが要る）")
    i3.add_argument("source_version_id", type=int)
    i3.add_argument("--model")
    i3.add_argument("--limit", type=int, default=20)
    i3.set_defaults(func=cmd_images)

    sp = sub.add_parser("rederive", help="保存済みの生HTMLから本文を作り直す（再取得しない）")
    sp.add_argument("source_version_id", type=int, nargs="?")
    sp.set_defaults(func=cmd_rederive)

    sp = sub.add_parser("source-kind", help="取り込み済みの出典の種別を変える")
    sp.add_argument("source_id", type=int)
    sp.add_argument("kind", choices=sorted(SOURCE_KINDS))
    sp.set_defaults(func=cmd_source_kind)

    sp = sub.add_parser("search", help="原文層の全文検索")
    sp.add_argument("query")
    sp.add_argument("--kind", choices=sorted(SOURCE_KINDS))
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="出典参照（版の本文を切り出す）")
    sp.add_argument("source_version_id", type=int)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--length", type=int, default=2000)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("claims", help="知識層の主張を検索")
    sp.add_argument("query", nargs="?")
    sp.add_argument("--entity")
    sp.add_argument("--kind", choices=sorted(SOURCE_KINDS))
    sp.add_argument("--verification", choices=sorted(VERIFICATIONS))
    sp.add_argument("--all", action="store_true", help="上書き済み・撤回済みも含める")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_claims)

    sp = sub.add_parser("propose", help="ある版の本文から主張候補を切り出す（登録はしない）")
    sp.add_argument("source_version_id", type=int)
    sp.add_argument("--entity", action="append")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--register", action="store_true", help="候補をそのまま主張として登録する")
    sp.add_argument("--allow-no-entity", action="store_true",
                    help="--register 時、エンティティに触れない文も登録する")
    sp.add_argument("--require-entity", action="store_true",
                    help="--register 時、エンティティに触れる文だけを登録する")
    sp.set_defaults(func=cmd_propose)

    cp = sub.add_parser("claim", help="主張の登録・関係付け")
    csub = cp.add_subparsers(dest="claim_command", required=True)
    c1 = csub.add_parser("add")
    c1.add_argument("--text", required=True)
    c1.add_argument("--source-version", type=int)
    c1.add_argument("--verification", choices=sorted(VERIFICATIONS))
    c1.add_argument("--entity", action="append")
    c1.add_argument("--locator")
    c1.add_argument("--note")
    c1.add_argument("--supersedes", type=int, help="この主張IDを上書きする")
    c1.set_defaults(func=cmd_claim_add)
    c2 = csub.add_parser("link")
    c2.add_argument("from_id", type=int)
    c2.add_argument("type", choices=["supersedes", "contradicts", "supports"])
    c2.add_argument("to_id", type=int)
    c2.add_argument("--note")
    c2.set_defaults(func=cmd_claim_link)
    c3 = csub.add_parser("set-verification")
    c3.add_argument("claim_id", type=int)
    c3.add_argument("verification", choices=sorted(VERIFICATIONS))
    c3.add_argument("--note")
    c3.set_defaults(func=cmd_claim_verify)

    ep = sub.add_parser("entity", help="エンティティの登録・一覧")
    esub = ep.add_subparsers(dest="entity_command", required=True)
    e1 = esub.add_parser("add")
    e1.add_argument("name")
    e1.add_argument("--kind")
    e1.add_argument("--alias", action="append")
    e1.add_argument("--note")
    e1.set_defaults(func=cmd_entity)
    e2 = esub.add_parser("list")
    e2.set_defaults(func=cmd_entity)
    e4 = esub.add_parser("coverage", help="エンティティごとの情報の厚みを見る")
    e4.add_argument("--kind", help="character などで絞る")
    e4.set_defaults(func=cmd_entity)
    e3 = esub.add_parser("related")
    e3.add_argument("name")
    e3.set_defaults(func=cmd_entity)

    sp = sub.add_parser("source-extract", help="その出典の本文をどこまで主張にするか")
    sp.add_argument("source_id", type=int)
    sp.add_argument("mode", choices=["full", "entity_only", "auto"],
                    help="full=人物名の無い文も採る / entity_only=触れる文だけ / auto=出典種別に任せる")
    sp.set_defaults(func=cmd_source_extract)

    sp = sub.add_parser("segment", help="出典がどの作品についての記述かを分ける（区分）")
    gsub = sp.add_subparsers(dest="segment_command")
    sp.set_defaults(func=cmd_segment, segment_command=None)
    g1 = gsub.add_parser("list", help="区分ごとの出典数")
    g1.set_defaults(func=cmd_segment)
    g2 = gsub.add_parser("rule", help="URLから区分を決めるルール")
    g2.add_argument("pattern", nargs="?")
    g2.add_argument("segment", nargs="?")
    g2.set_defaults(func=cmd_segment)
    g3 = gsub.add_parser("set", help="出典の区分を直接指定する")
    g3.add_argument("source_id", type=int)
    g3.add_argument("segment", nargs="?")
    g3.set_defaults(func=cmd_segment)
    g4 = gsub.add_parser("apply", help="ルールを既存の出典に当てはめる")
    g4.set_defaults(func=cmd_segment)

    sp = sub.add_parser("rule", help="URLから出典種別を決めるルール")
    sp.add_argument("pattern", nargs="?")
    sp.add_argument("kind", nargs="?", choices=sorted(SOURCE_KINDS))
    sp.set_defaults(func=cmd_rule)

    sp = sub.add_parser("ask", help="質問する（APIキーがあれば回答、無ければプロンプトを出力）")
    sp.add_argument("question")
    sp.add_argument("--model")
    sp.add_argument("--no-llm", action="store_true")
    sp.add_argument("--no-plan", action="store_true",
                    help="質問を対象ごとに分けず、一発で引く")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("serve", help="ブラウザUIを起動する")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (StoreError, FileNotFoundError, FileExistsError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:  # head などで出力が打ち切られた場合
        return 0
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
