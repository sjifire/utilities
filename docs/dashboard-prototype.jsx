import { useState } from "react";
/* ===== LIVE DATA — replace values on refresh ===== */
const LIVE_DATA = {
  timestamp: "2026-02-13T06:37:21Z", platoon: "A Platoon",
  crew: [
    { name: "Jane Smith", position: "Captain", section: "S31", shift: "0800-1800" },
    { name: "John Doe", position: "Lieutenant", section: "S31", shift: "1800-1800" },
    { name: "Alex Johnson", position: "Apparatus Operator", section: "S31", shift: "1800-1800" },
    { name: "Jane Smith", position: "Chief", section: "Chief Officer", shift: "1800-0800" },
    { name: "Pat Williams", position: "Marine: Pilot", section: "FB31", shift: "1800-1800" },
  ],
  chiefOfficer: "Smith", openCalls: 0,
  recentCalls: [
    { id: "26-999001", nature: "Fire-Structure", address: "100 Sample St", date: "Feb 12", time: "14:38", severity: "medium", note: "Structure fire", neris: null },
    { id: "26-999002", nature: "CPR ALS", address: "200 Example Ave", date: "Feb 10", time: "22:47", severity: "high", note: "Cardiac/ALS", neris: null },
    { id: "26-999003", nature: "Accident-Injury", address: "Main St & 1st Ave", date: "Feb 8", time: "22:04", severity: "high", note: "MVC", neris: { id: "FD00000000|26999003|0000000001", status: "PENDING_APPROVAL" } },
    { id: "26-999004", nature: "Fire-Chimney", address: "300 Demo Rd", date: "Feb 7", time: "13:45", severity: "medium", note: "Chimney fire", neris: { id: "FD00000000|26999004|0000000002", status: "PENDING_APPROVAL" } },
    { id: "26-999005", nature: "Fire-Alarm", address: "400 Test Dr #45", date: "Feb 7", time: "01:42", severity: "low", note: "Fire alarm", neris: null },
    { id: "26-999006", nature: "Fire-Vehicle", address: "500 Placeholder Ln", date: "Jan 23", time: "10:03", severity: "medium", note: "Vehicle fire", neris: null },
    { id: "26-999007", nature: "Animal Sick", address: "600 Mockup Way", date: "Jan 21", time: "23:31", severity: "low", note: "Sick animal", neris: null },
    { id: "26-999008", nature: "Fire-Burn Inv", address: "700 Template Ct", date: "Jan 18", time: "16:24", severity: "low", note: "Burn investigation", neris: null },
  ],
  localReports: 0,
};
/* ===== END LIVE DATA ===== */

const CSS = `
*{box-sizing:border-box;margin:0;padding:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
:root{
  --b1:rgba(255,255,255,.04);--b2:rgba(255,255,255,.06);--b3:rgba(255,255,255,.08);--b4:rgba(255,255,255,.1);
  --r:#dc2626;--g:#4ade80;--bl:#60a5fa;--am:#fbbf24;--am2:#f59e0b;--pu:#c084fc;--gr:#6b7280;--err:#f87171;
  --bg1:#162033;--bg2:#1a2744;
  --t1:#fff;--t2:#e2e8f0;--t3:#94a3b8;--t4:#64748b;--t5:#475569;
  --blA:rgba(96,165,250,.12);--amA:rgba(245,158,11,.12);--gA:rgba(34,197,94,.15);--rA:rgba(220,38,38,.15);--puA:rgba(192,132,252,.12)
}
.root{min-height:100vh;background:#0c1829;font-family:'Segoe UI','Helvetica Neue',sans-serif;color:var(--t2)}
.t1{color:var(--t1)}.t2{color:var(--t2)}.t3{color:var(--t3)}.t4{color:var(--t4)}.t5{color:var(--t5)}
.grn-t{color:var(--g)}.amb-t{color:var(--am)}.blu-t{color:var(--bl)}
.f10{font-size:10px}.f11{font-size:11px}.f12{font-size:12px}.f13{font-size:13px}.f14{font-size:14px}.f20{font-size:20px}.f28{font-size:28px}
.b{font-weight:600}.bb{font-weight:700}.mono{font-family:monospace}.nw{white-space:nowrap}.lh1{line-height:1}.lh15{line-height:1.5}
.up{text-transform:uppercase;letter-spacing:.08em}.tar{text-align:right}
.mt2{margin-top:2px}.mt4{margin-top:4px}.mt6{margin-top:6px}.mb8{margin-bottom:8px}.mb16{margin-bottom:16px}.mb24{margin-bottom:24px}.mt20{margin-top:20px}
.fx{display:flex}.fx-s{display:flex;justify-content:space-between;align-items:flex-start}.gap10{gap:10px}
.wrap{max-width:1200px;margin:0 auto}
.g4,.g3,.g-ov{display:grid;gap:16px}
.g4{grid-template-columns:repeat(4,1fr)}.g3{grid-template-columns:repeat(3,1fr)}.g-ov{grid-template-columns:1fr 1.6fr;gap:20px}
.hdr{background:linear-gradient(135deg,#0f2240,#1a3a5c);border-bottom:3px solid var(--r)}
.hdr-inner{padding:12px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.logo{letter-spacing:.02em;line-height:1.2}
.status{display:inline-flex;align-items:center;gap:6px;margin-top:4px;border-radius:20px;padding:3px 12px;font-size:12px;font-weight:600;border:1px solid;
  &.ok{background:var(--gA);border-color:rgba(34,197,94,.3);color:var(--g);& .pulse{background:var(--g)}}
  &.active{background:var(--rA);border-color:rgba(220,38,38,.3);color:var(--err);& .pulse{background:var(--err)}}
}
.pulse{width:7px;height:7px;border-radius:50%;display:inline-block;animation:pulse 2s ease-in-out infinite}
.updated-row{margin-top:6px;display:flex;align-items:center;justify-content:flex-end;gap:8px}
.refresh-hint{font-size:11px;background:var(--blA);color:var(--bl);padding:2px 10px;border-radius:12px;font-weight:600}
.nav{background:#111d32;border-bottom:1px solid var(--b2);padding:0 24px}
.tab{background:none;border:none;border-bottom:2px solid transparent;color:var(--t4);padding:12px 20px;font-size:13px;font-weight:600;cursor:pointer;text-transform:uppercase;letter-spacing:.06em;
  &.on{border-bottom-color:var(--r);color:var(--t1)}
}
.pnl{background:var(--bg1);border:1px solid var(--b2);border-radius:8px;overflow:hidden}
.phdr{padding:14px 18px;border-bottom:1px solid var(--b2);display:flex;justify-content:space-between;align-items:center}
.main{padding:24px}
.stat{background:linear-gradient(135deg,var(--bg1),var(--bg2));border:1px solid var(--b2);border-radius:8px;padding:20px 18px;border-left:3px solid;
  &[data-a="grn"]{border-left-color:var(--g)}
  &[data-a="blu"]{border-left-color:var(--bl)}
  &[data-a="amb"]{border-left-color:var(--am2)}
  &[data-a="pur"]{border-left-color:var(--pu)}
}
.bdg{font-size:11px;padding:3px 10px;border-radius:4px;font-weight:600;background:var(--blA);color:var(--bl);
  &.amb{background:var(--amA);color:var(--am)}
  &.grn{background:var(--gA);color:var(--g)}
  &[data-p="Captain"]{background:var(--rA);color:var(--err)}
  &[data-p="Lieutenant"]{background:var(--amA);color:var(--am)}
  &[data-p="Chief"]{background:var(--puA);color:var(--pu)}
}
.orow{padding:12px 18px;border-bottom:1px solid var(--b1);display:flex;justify-content:space-between;align-items:center;&:last-child{border-bottom:none}}
.call{padding:14px 18px;border-left:3px solid;border-bottom:1px solid var(--b1);
  &:last-child{border-bottom:none}
  &[data-s="high"]{border-left-color:var(--r);background:rgba(220,38,38,.1)}
  &[data-s="medium"]{border-left-color:var(--am2);background:rgba(245,158,11,.08)}
  &[data-s="low"]{border-left-color:var(--gr);background:rgba(107,114,128,.08)}
}
.call-r{text-align:right;flex-shrink:0}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;
  &[data-s="high"]{background:var(--r)}
  &[data-s="medium"]{background:var(--am2)}
  &[data-s="low"]{background:var(--gr)}
}
.tbl-wrap{overflow-x:auto}
.tbl{width:100%;border-collapse:collapse;font-size:13px;
  & thead tr{border-bottom:1px solid var(--b3)}
  & tbody tr{border-bottom:1px solid var(--b1);&:nth-child(even){background:rgba(255,255,255,.015)}}
}
.th{padding:10px 14px;text-align:left;font-size:11px;color:var(--t4);text-transform:uppercase;letter-spacing:.06em;font-weight:600;white-space:nowrap}
.td{padding:12px 14px}.dot-td{width:30px}
.sechdr{padding:8px 18px;background:rgba(255,255,255,.02);border-bottom:1px solid var(--b1);font-size:11px;color:var(--t4);text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.crow{padding:14px 18px;border-bottom:1px solid var(--b1);display:grid;grid-template-columns:2fr 1.2fr 1fr;align-items:center}
.empty{padding:32px 18px;text-align:center;color:var(--t5);font-size:13px}
.rbtn{font-size:11px;padding:5px 12px;border-radius:4px;font-weight:600;cursor:pointer;background:rgba(245,158,11,.15);color:var(--am);border:1px solid rgba(245,158,11,.3);
  &.n{background:rgba(96,165,250,.15);color:var(--bl);border-color:rgba(96,165,250,.3)}
}
.rhint{margin-top:6px;font-size:11px;color:var(--t3);background:rgba(255,255,255,.05);border:1px solid var(--b4);border-radius:4px;padding:6px 10px}
.help-row{padding:14px 18px;border-top:1px solid var(--b1);display:flex;gap:16px;align-items:flex-start}
.help-lbl{flex-shrink:0;min-width:280px}
.help-cmd{font-size:12px;background:rgba(96,165,250,.1);color:var(--bl);padding:4px 10px;border-radius:4px;font-family:'SF Mono','Fira Code',monospace;font-weight:600}
.help-intro{padding:16px 18px 8px}
.help-foot{padding:16px 18px;border-top:1px solid var(--b2)}
.footer{padding:16px 24px 32px;display:flex;justify-content:space-between;font-size:11px;color:var(--t5)}
`;

const D = LIVE_DATA;
const uniqueCrew = [...new Map(D.crew.map(c=>[c.name,c])).values()];
const last5 = D.recentCalls.slice(0,5);
const nerisCount = D.recentCalls.filter(c=>c.neris).length;
const ic = n=>n.includes("CPR")||n.includes("ALS")?"🚑":n.includes("Accident")?"🚗":n.includes("Structure")?"🔥":n.includes("Chimney")?"🏠":n.includes("Alarm")?"🔔":n.includes("Vehicle")?"🚒":n.includes("Animal")?"🐾":n.includes("Burn")?"🔍":"📟";
const SECS = [{key:"S31",label:"Station 31"},{key:"Chief Officer",label:"Chief Officer"},{key:"FB31",label:"Fireboat 31 Standby"},{key:"Support",label:"Support Standby"}];
const HELP = [["refresh","Refresh dashboard"],["Start a report for 26-XXXXXX","Create incident report"],["Import NERIS report for 26-XXXXXX","Pull NERIS report into draft"],["Show me call 26-XXXXXX","Get dispatch call details"],["Who was on duty Jan 15?","Look up crew for any date"],["List incidents","Show draft/in-progress reports"],["Submit incident for [ID]","Submit to NERIS"],["Search calls from Jan 1 to Jan 31","Search dispatch archive"],["Show open calls","Check active calls"]];

const Bdg = ({text,cls})=><span className={`bdg ${cls||""}`}>{text}</span>;
const Stat = ({label,value,accent,sub})=>(<div className="stat" data-a={accent}><div className="up t4 f11 mb8">{label}</div><div className="bb f28 t1 lh1">{value}</div><div className="f12 t3 mt6">{sub}</div></div>);
const Panel = ({title,right,children})=>(<div className="pnl"><div className="phdr"><span className="f14 b t1">{title}</span>{right&&<span className="f11 t4">{right}</span>}</div>{children}</div>);
const CallRow = ({c,children})=>(<tr>
  <td className="td dot-td"><span className="dot" data-s={c.severity}/></td>
  <td className="td mono t3 f12">{c.id}</td>
  <td className="td t2 nw">{c.date} {c.time}</td>
  <td className="td t1 b nw">{ic(c.nature)} {c.nature}</td>
  <td className="td t3">{c.address}</td>
  {children}
</tr>);

export default function Dashboard() {
  const [tab,setTab] = useState("overview");
  const [hint,setHint] = useState(null);
  const now = new Date();
  const isBiz = now.getHours()>=8&&now.getHours()<18;
  const updated = new Date(D.timestamp).toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"});
  const TABS = {overview:"Overview",calls:"Recent Calls",crew:"On Duty",reporting:"Reporting",help:"Help"};

  const rptBtn = (c)=>{
    const isN=!!c.neris,lbl=isN?"Import from NERIS":"Start Report",prompt=isN?`Import NERIS report for ${c.id}`:`Start a report for ${c.id}`;
    return(<div><button className={`rbtn ${isN?"n":""}`} onClick={()=>setHint(hint===c.id?null:c.id)}>{lbl}</button>
    {hint===c.id&&<div className="rhint">Ask Claude: <strong className={isN?"blu-t":"amb-t"}>{prompt}</strong></div>}</div>);
  };

  return (
    <div className="root">
      <style>{CSS}</style>

      <header className="hdr">
        <div className="wrap hdr-inner">
          <div>
            <div className="bb f20 t1 logo">SJIF&R Operations Dashboard</div>
            <div className="up f11 t3 mt2">San Juan Island Fire & Rescue</div>
          </div>
          <div className="tar">
            <div className="f13 t3">Wednesday, February 12, 2026</div>
            <div className={`status ${D.openCalls?"active":"ok"}`}>
              <span className="pulse"/>{D.openCalls?`${D.openCalls} Active Call${D.openCalls>1?"s":""}`:"No Active Calls"}
            </div>
            <div className="updated-row">
              <span className="f11 t4">Updated {updated}</span>
              <span className="refresh-hint">say "refresh" to update</span>
            </div>
          </div>
        </div>
      </header>

      <nav className="nav">
        <div className="wrap fx">
          {Object.entries(TABS).map(([k,v])=><button key={k} className={`tab ${tab===k?"on":""}`} onClick={()=>setTab(k)}>{v}</button>)}
        </div>
      </nav>

      <main className="wrap main">

        {tab==="overview"&&(<div>
          <div className="g4 mb24">
            <Stat label="Open Calls" value={String(D.openCalls)} accent="grn" sub="All clear"/>
            <Stat label="On Duty" value={String(uniqueCrew.length)} accent="blu" sub={D.platoon}/>
            <Stat label="Calls (7 days)" value={String(last5.length)} accent="amb" sub={`${last5.filter(c=>c.neris).length} with NERIS reports`}/>
            <Stat label="Duty Officer" value={D.chiefOfficer} accent="pur" sub="Chief Officer"/>
          </div>
          <div className="g-ov">
            <Panel title={`On Duty — ${D.platoon}`} right="Feb 12-13">
              {D.crew.map((c,i)=>(<div key={i} className="orow"><div><div className="f13 b t2">{c.name}</div><div className="f11 t4 mt2">{c.position}</div></div><Bdg text={c.section}/></div>))}
            </Panel>
            <Panel title="Recent Calls" right={`${last5.length} calls`}>
              {last5.map((c,i)=>(<div key={i} className="call" data-s={c.severity}>
                <div className="fx-s">
                  <div className="fx gap10"><span className="f20 lh1">{ic(c.nature)}</span>
                    <div><div className="f13 b t2">{c.nature}</div><div className="f12 t3 mt2">{c.address}</div><div className="f11 t4 mt4">{c.note}</div></div>
                  </div>
                  <div className="call-r">
                    <div className="f12 t3 b">{c.date}</div><div className="f11 t4">{c.time}</div>
                    <div className="f10 t5 mt4 mono">{c.id}</div>
                    <div className="mt4">{c.neris?<span className="f11 grn-t">✓ NERIS</span>:<span className="f11 amb-t">⚠ No report</span>}</div>
                  </div>
                </div>
              </div>))}
            </Panel>
          </div>
        </div>)}

        {tab==="calls"&&(
          <Panel title="Dispatch Log" right={`${D.recentCalls.length} calls`}>
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr>{["","ID","Date/Time","Nature","Address","Report","Notes"].map(h=><th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{D.recentCalls.map((c,i)=>(<CallRow key={i} c={c}>
                <td className="td">{c.neris?<span className="f11 grn-t b">✓ NERIS</span>:<span className="f11 amb-t">⚠ Missing</span>}</td>
                <td className="td t4 f12">{c.note}</td>
              </CallRow>))}</tbody>
            </table></div>
          </Panel>
        )}

        {tab==="crew"&&(
          <Panel title={`${D.platoon} — February 12-13, 2026`} right={<Bdg text={isBiz?"Day Shift":"Night Shift"} cls={isBiz?"grn":"blu"}/>}>
            {SECS.map(({key,label})=>{const m=D.crew.filter(c=>c.section===key);if(!m.length)return null;return(<div key={key}>
              <div className="sechdr">{label}</div>
              {m.map((c,j)=>(<div key={j} className="crow">
                <span className="f14 b t2">{c.name}</span>
                <span className="bdg" data-p={c.position}>{c.position}</span>
                <span className="f12 t4 mono tar">{c.shift}</span>
              </div>))}
            </div>);})}
          </Panel>
        )}

        {tab==="reporting"&&(<div>
          <div className="g3 mb24">
            <Stat label="In Progress" value={String(D.localReports)} accent="blu" sub="Drafts & reviews"/>
            <Stat label="Missing Reports" value={String(D.recentCalls.length)} accent="amb" sub="From dispatch log"/>
            <Stat label="In NERIS" value={String(nerisCount)} accent="grn" sub="Available to import"/>
          </div>
          <Panel title="In-Progress Reports" right={<Bdg text="Draft / In Progress / Review"/>}>
            <div className="empty">No incident reports in progress.</div>
          </Panel>
          <div className="mt20">
            <Panel title="Calls Needing Reports" right={<Bdg text={`${D.recentCalls.length} calls`} cls="amb"/>}>
              <div className="tbl-wrap"><table className="tbl">
                <thead><tr>{["","ID","Date","Nature","Address","NERIS","Action"].map(h=><th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{D.recentCalls.map((c,i)=>(<CallRow key={i} c={c}>
                  <td className="td">{c.neris?<span className="f11 grn-t">✓ {c.neris.status.replace(/_/g," ")}</span>:<span className="f11 t5">—</span>}</td>
                  <td className="td">{rptBtn(c)}</td>
                </CallRow>))}</tbody>
              </table></div>
            </Panel>
          </div>
        </div>)}

        {tab==="help"&&(
          <Panel title="Claude Commands" right="Type these in the chat">
            <div className="help-intro"><p className="f13 t3 mb16 lh15">Use these commands to interact with dispatch, crew, and reporting.</p></div>
            {HELP.map(([cmd,desc],i)=>(<div key={i} className="help-row">
              <div className="help-lbl"><code className="help-cmd">{cmd}</code></div>
              <div className="f12 t3 lh15">{desc}</div>
            </div>))}
            <div className="help-foot"><p className="f12 t5 lh15">You can also ask Claude anything in natural language.</p></div>
          </Panel>
        )}

      </main>
      <footer className="wrap footer"><span>San Juan County Fire District 3 · 1011 Mullis Street, Friday Harbor, WA 98250</span><span>© 2026 SJIF&R</span></footer>
    </div>
  );
}
