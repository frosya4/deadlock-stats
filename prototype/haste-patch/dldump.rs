use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufReader, BufWriter, Write};
use anyhow::{Context as _, Result};
use haste::demofile::DemoFile;
use haste::entities::{DeltaHeader, Entity};
use haste::fieldvalue::FieldValue;
use haste::parser::{Context, Parser, Visitor};

fn fv_json(v: &FieldValue) -> String {
    match v {
        FieldValue::I64(x) => x.to_string(), FieldValue::U64(x) => format!("\"{}\"", x),
        FieldValue::F32(x) => if x.is_finite() { x.to_string() } else { "null".into() },
        FieldValue::Bool(x) => x.to_string(),
        FieldValue::String(b) | FieldValue::BinaryBlock(b) => serde_json::to_string(&String::from_utf8_lossy(b)).unwrap(),
        other => serde_json::to_string(&format!("{:?}", other)).unwrap(),
    }
}
fn ent_json(e: &Entity) -> String {
    let mut parts = Vec::new();
    for (k, v) in e.iter() {
        let name = match e.get_path(k) { Some(p) => {
            let mut names: Vec<String> = Vec::new();
            let mut cur = p.get(0).and_then(|i| e.serializer().get_child(i));
            if let Some(f) = cur { names.push(f.var_name.str.to_string()); }
            for (n, i) in p.iter().enumerate().skip(1) {
                let i = *i as usize;
                let nxt = cur.and_then(|f| f.get_child(i));
                match nxt { Some(c) => { let s = c.var_name.str.to_string();
                        if names.last().map(|l| l == &s).unwrap_or(false) { names.push(format!("[{}]", i)); } else { names.push(s); } }
                    None => names.push(format!("[{}]", i)) }
                let _ = n; cur = nxt;
            }
            names.join(".") }, None => k.to_string() };
        parts.push(format!("{}:{}", serde_json::to_string(&name).unwrap(), fv_json(v)));
    }
    format!("{{{}}}", parts.join(","))
}

struct V { out: BufWriter<File>, msgs: BufWriter<File>, last_dump: i32, interval: i32, full: HashSet<String>, seen: HashMap<String, (i32, i32, i32)>, alive: HashMap<i32, String> }
impl V {
    fn dump(&mut self, ctx: &Context, tag: &str) -> Result<()> {
        if let Some(ents) = ctx.entities() {
            for (idx, e) in ents.iter() {
                let cls = e.serializer().serializer_name.str.to_string();
                if self.full.contains(&cls) || cls.contains("Boss") || cls.contains("Guardian") || cls.contains("Shrine") || cls.contains("Barrack") || cls.contains("Patron") || cls.contains("Titan") || cls.contains("Idol") || cls.contains("Team") {
                    writeln!(self.out, "{{\"tag\":\"{}\",\"tick\":{},\"idx\":{},\"cls\":\"{}\",\"f\":{}}}", tag, ctx.tick(), idx, cls, ent_json(e))?;
                }
            }
        }
        Ok(())
    }
}
impl Visitor for V {
    fn on_entity(&mut self, ctx: &Context, dh: DeltaHeader, e: &Entity) -> Result<()> {
        let cls = e.serializer().serializer_name.str.to_string();
        let t = ctx.tick();
        let s = self.seen.entry(cls.clone()).or_insert((0, t, t)); s.2 = t;
        if !self.alive.contains_key(&e.index()) || self.alive.get(&e.index()) != Some(&cls) { s.0 += 1; self.alive.insert(e.index(), cls.clone()); }
        if dh == DeltaHeader::DELETE || dh == DeltaHeader::LEAVE { 
            if cls.contains("Boss") || cls.contains("Guardian") || cls.contains("Shrine") || cls.contains("Barrack") || cls.contains("Patron") || cls.contains("Titan") || cls.contains("Idol") || cls.contains("Walker") {
                writeln!(self.out, "{{\"tag\":\"del\",\"dh\":\"{:?}\",\"tick\":{},\"idx\":{},\"cls\":\"{}\",\"f\":{}}}", dh, t, e.index(), cls, ent_json(e))?; }
            if dh == DeltaHeader::DELETE { self.alive.remove(&e.index()); }
        }
        Ok(())
    }
    fn on_packet(&mut self, ctx: &Context, pt: u32, data: &[u8]) -> Result<()> {
        if (300..400).contains(&pt) || pt == 205 || pt == 207 || (pt >= 100 && pt < 200 && pt != 145) {
            if pt == 300 || pt == 323 || pt == 340 || pt == 341 || pt == 342 || pt == 343 || pt == 344 { return Ok(()); }
            let hex: String = data.iter().map(|b| format!("{:02x}", b)).collect();
            writeln!(self.msgs, "{}\t{}\t{}", ctx.tick(), pt, hex)?;
        }
        Ok(())
    }
    fn on_tick_end(&mut self, ctx: &Context) -> Result<()> {
        if ctx.tick() - self.last_dump >= self.interval { self.last_dump = ctx.tick(); self.dump(ctx, "snap")?; }
        Ok(())
    }
}
fn main() -> Result<()> {
    let a: Vec<String> = std::env::args().collect();
    let path = a.get(1).context("usage")?; let outdir = a.get(2).context("outdir")?;
    let full: HashSet<String> = ["CCitadelPlayerController","CCitadelPlayerPawn","CCitadelGameRulesProxy"].iter().map(|s| s.to_string()).collect();
    let v = V { out: BufWriter::new(File::create(format!("{}/ents.jsonl", outdir))?), msgs: BufWriter::new(File::create(format!("{}/msgs.tsv", outdir))?), last_dump: -100000, interval: 64*30, full, seen: HashMap::new(), alive: HashMap::new() };
    let demo = DemoFile::start_reading(BufReader::new(File::open(path)?))?;
    let mut p = Parser::from_stream_with_visitor(demo, v)?;
    p.run_to_end()?;
    let ctx_tick = p.context().tick();
    // final dump
    let ctx = p.context() as *const Context;
    let vis = p.visitor_mut();
    vis.dump(unsafe { &*ctx }, "final")?;
    let mut cls: Vec<_> = vis.seen.iter().collect(); cls.sort();
    let mut f = File::create(format!("{}/classes.tsv", outdir))?;
    for (c, (n, a, b)) in cls { writeln!(f, "{}\t{}\t{}\t{}", c, n, a, b)?; }
    vis.out.flush()?; vis.msgs.flush()?;
    eprintln!("done tick {}", ctx_tick);
    Ok(())
}
