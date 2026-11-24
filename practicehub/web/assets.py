"""Static assets for the web UI: one stylesheet and one script, served from /static.

The layout follows the familiar shape of a work tracker: a top bar, a sidebar of places, a board of columns, and a
work item page with the content on the left and its details on the right. The look borrows common work-tracker
design patterns (tokens, lozenges, section messages, modals) and uses the system font stack. The script draws
workflow diagrams, runs the visual workflow editor, the avatar menu and the confirmation dialog. It never evaluates
anything: the editor only builds TOML text, and the server checks it.
"""

# Dark tokens, used twice: for the system preference and for an explicit data-theme="dark".
_DARK = """color-scheme:dark;
--bg:#1D2125;--surface:#1D2125;--raised:#22272B;--raised-hover:#282E33;--overlay:#282E33;--sunken:#161A1D;
--fg:#C7D1DB;--subtle:#A3B0BF;--subtlest:#96A2B4;--border:#66727F;--border-bold:#8696A7;
--input-border:#6A7683;--control-border:#66727F;--card-edge:#66727F;--focus:#85B8FF;
--neutral:#A1BDD91A;--neutral-hover:#A6C5E233;--neutral-pressed:#BFDBF852;
--disabled-bg:#BCD6F00A;--disabled-fg:#BFDBF870;
--brand:#579DFF;--brand-hover:#85B8FF;--brand-pressed:#CCE0FF;--brand-fg:#1D2125;
--selected-bg:#1C2B41;--selected-fg:#75AEFF;--link:#75AEFF;--link-hover:#A3C9FF;
--input-bg:#22272B;--input-hover:#282E33;
--danger:#F87168;--danger-hover:#FD9891;--danger-on:#1D2125;--danger-bg:#42221F;--danger-fg:#FD9891;--danger-icon:#F87168;
--success:#4BCE97;--success-bg:#1C3329;--success-fg:#7EE2B8;--success-icon:#4BCE97;
--warning:#F5CD47;--warning-bg:#332E1B;--warning-fg:#FEC195;--warning-icon:#F38A3F;
--info-bg:#1C2B41;--info-fg:#85B8FF;--info-icon:#579DFF;
--discovery:#9F8FEF;--discovery-bg:#2B273F;--discovery-fg:#B8ACF6;
--loz-default-bg:#A1BDD914;--loz-default-fg:#9FADBC;--loz-inprogress-bg:#1C2B41;--loz-inprogress-fg:#85B8FF;
--loz-success-bg:#1C3329;--loz-success-fg:#7EE2B8;--loz-removed-bg:#42221F;--loz-removed-fg:#FD9891;
--loz-moved-bg:#332E1B;--loz-moved-fg:#F5CD47;--loz-new-bg:#2B273F;--loz-new-fg:#B8ACF6;
--lozb-default-bg:#8C9BAB;--lozb-default-fg:#1D2125;--lozb-inprogress-bg:#579DFF;--lozb-inprogress-fg:#1D2125;
--lozb-success-bg:#4BCE97;--lozb-success-fg:#1D2125;--lozb-removed-bg:#F87168;--lozb-removed-fg:#1D2125;
--lozb-moved-bg:#F5CD47;--lozb-moved-fg:#1D2125;--lozb-new-bg:#9F8FEF;--lozb-new-fg:#1D2125;
--ki-task:#579DFF;--ki-gate:#9F8FEF;--ki-notice:#F38A3F;--ki-hold:#F87168;--ki-automation:#8C9BAB;--ki-fg:#1D2125;
--service:#FEC195;--service-bg:#332E1B;
--shadow-raised:0 0 0 1px var(--card-edge),0 1px 1px #03040480;
--shadow-overlay:0 0 0 1px #BCD6F00A,0 8px 12px #0304045C,0 0 1px 1px #03040480;
--backdrop:#10121499;
"""

CSS = (r"""
:root{color-scheme:light;
--bg:#FFFFFF;--surface:#FFFFFF;--raised:#FFFFFF;--raised-hover:#F1F2F4;--overlay:#FFFFFF;--sunken:#F7F8F9;
--fg:#172B4D;--subtle:#44546F;--subtlest:#5A6579;--border:#828C9E;--border-bold:#6B778C;
--input-border:#7D8799;--control-border:#828C9E;--card-edge:#828C9E;--focus:#0C66E4;
--neutral:#091E4214;--neutral-hover:#091E422B;--neutral-pressed:#091E4252;
--disabled-bg:#091E4208;--disabled-fg:#091E4280;
--brand:#0C66E4;--brand-hover:#0055CC;--brand-pressed:#09326C;--brand-fg:#FFFFFF;
--selected-bg:#E9F2FF;--selected-fg:#0055CC;--link:#0055CC;--link-hover:#09326C;
--input-bg:#F7F8F9;--input-hover:#FFFFFF;
--danger:#C9372C;--danger-hover:#AE2E24;--danger-on:#FFFFFF;--danger-bg:#FFECEB;--danger-fg:#AE2E24;--danger-icon:#C9372C;
--success:#1F845A;--success-bg:#DCFFF1;--success-fg:#216E4E;--success-icon:#1F845A;
--warning:#E2B203;--warning-bg:#FFF7D6;--warning-fg:#A54800;--warning-icon:#E56910;
--info-bg:#E9F2FF;--info-fg:#0055CC;--info-icon:#0C66E4;
--discovery:#6E5DC6;--discovery-bg:#F3F0FF;--discovery-fg:#5E4DB2;
--loz-default-bg:#091E420F;--loz-default-fg:#44546F;--loz-inprogress-bg:#E9F2FF;--loz-inprogress-fg:#0055CC;
--loz-success-bg:#DCFFF1;--loz-success-fg:#216E4E;--loz-removed-bg:#FFECEB;--loz-removed-fg:#AE2E24;
--loz-moved-bg:#FFF7D6;--loz-moved-fg:#A54800;--loz-new-bg:#F3F0FF;--loz-new-fg:#5E4DB2;
--lozb-default-bg:#44546F;--lozb-default-fg:#FFFFFF;--lozb-inprogress-bg:#0C66E4;--lozb-inprogress-fg:#FFFFFF;
--lozb-success-bg:#1F845A;--lozb-success-fg:#FFFFFF;--lozb-removed-bg:#C9372C;--lozb-removed-fg:#FFFFFF;
--lozb-moved-bg:#F5CD47;--lozb-moved-fg:#172B4D;--lozb-new-bg:#6E5DC6;--lozb-new-fg:#FFFFFF;
--ki-task:#0C66E4;--ki-gate:#6E5DC6;--ki-notice:#A54800;--ki-hold:#C9372C;--ki-automation:#626F86;--ki-fg:#FFFFFF;
--service:#A54800;--service-bg:#FFF7D6;
--shadow-raised:0 0 0 1px var(--card-edge),0 1px 1px #091E4240;
--shadow-overlay:0 8px 12px #091E4226,0 0 1px #091E424F;
--backdrop:#091E427A;
/* older names, kept so existing markup and the diagram keep working; they follow the tokens above */
--muted:var(--subtlest);--line:var(--border);--line-2:var(--border-bold);--hover:var(--neutral);--brand-soft:var(--selected-bg);
--shadow:var(--shadow-raised);--todo:var(--loz-default-fg);--todo-bg:var(--loz-default-bg);
--prog:var(--loz-inprogress-fg);--prog-bg:var(--loz-inprogress-bg);--wait:var(--loz-moved-fg);--wait-bg:var(--loz-moved-bg);
--done:var(--success-fg);--done-bg:var(--success-bg);--bad:var(--danger-fg);--bad-bg:var(--danger-bg);
--agent:var(--discovery-fg);--agent-bg:var(--discovery-bg);--person:var(--success-fg);--person-bg:var(--success-bg)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){""" + _DARK + r"""}}
:root[data-theme=dark]{""" + _DARK + r"""}

/* ---- base and type */
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:400 14px/20px system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,
"Helvetica Neue",Arial,sans-serif;overflow-wrap:break-word}
a{color:var(--link);text-decoration:none}a:hover{color:var(--link-hover);text-decoration:underline}
h1{font-size:24px;line-height:28px;font-weight:600;margin:0 0 16px}
h2{font-size:16px;line-height:20px;font-weight:600;margin:24px 0 8px}
h3{font-size:14px;line-height:16px;font-weight:600;margin:16px 0 4px}
small,.small{font-size:12px;line-height:16px}
p{margin:0 0 12px}
code{font:12px/16px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--neutral);padding:0 4px;
border-radius:3px;overflow-wrap:anywhere}
[hidden]{display:none!important}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.visually-hidden{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0}
.muted{color:var(--subtlest)}.off{color:var(--danger-fg);font-weight:600}
.req{color:var(--danger-fg);font-weight:600;margin-left:4px}
.req-note{font-size:12px;line-height:16px;margin:0}.req-note .req{margin-left:0}

/* ---- top bar */
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:8px;height:56px;padding:0 12px;
background:var(--surface);color:var(--fg);border-bottom:1px solid var(--border)}
.logo{display:flex;align-items:center;gap:8px;font-weight:600;color:var(--fg);white-space:nowrap;padding:4px 8px;
border-radius:3px}
a.logo:hover{background:var(--neutral);color:var(--fg);text-decoration:none}
.logo .mark{min-width:24px;width:auto;height:24px;padding:0 4px;border-radius:3px;background:var(--brand);
color:var(--brand-fg);display:grid;place-items:center;font-size:11px;font-weight:700}
.top form.search{flex:0 1 400px;margin:0 0 0 8px;min-width:0}
.top form.search input{margin:0}
.top .spacer{flex:1}
.menu-toggle{display:none}
.me{display:flex;align-items:center;gap:8px;white-space:nowrap}
/* avatar menu: <div class="avatar-menu"><button aria-haspopup="menu" aria-expanded="false">..</button><div role="menu" hidden>..</div></div> */
.avatar-menu{position:relative}
.practice-menu>button.logo{padding:4px 8px;height:auto;min-height:32px;font-weight:600}
.practice-menu>button.logo span{max-width:220px;overflow:hidden;text-overflow:ellipsis}
.practice-menu [role=menu]{left:0;right:auto}
.avatar-menu .menu-sec{padding:8px 16px 2px;font-size:11px;line-height:16px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--subtlest)}
h2.pick-head{font-size:14px;line-height:20px;margin:16px 0 2px}h2.pick-head:first-child{margin-top:0}
.avatar-menu>button{display:inline-flex;align-items:center;gap:8px;background:transparent;color:var(--fg);padding:0 8px 0 4px;
height:32px;border-radius:16px;font-weight:500}
.avatar-menu>button .chev{color:var(--subtle)}
.avatar-menu>button:hover,.avatar-menu>button[aria-expanded=true]{background:var(--neutral-hover)}
.avatar-menu>button .av{width:24px;height:24px}
.avatar-menu [role=menu]{position:absolute;right:0;top:calc(100% + 8px);z-index:50;min-width:240px;max-width:calc(100vw - 32px);
padding:4px 0;background:var(--overlay);border-radius:3px;box-shadow:var(--shadow-overlay)}
.avatar-menu .menu-head{padding:8px 16px;border-bottom:1px solid var(--border);margin-bottom:4px}
.avatar-menu .menu-head b{display:block}.avatar-menu .menu-head span{display:block;font-size:12px;line-height:16px;color:var(--subtlest)}
.avatar-menu [role=menuitem]{display:flex;align-items:center;gap:8px;width:100%;min-height:32px;padding:8px 16px;
color:var(--fg);background:transparent;border:0;border-radius:0;font:inherit;text-align:left;text-decoration:none;
box-shadow:inset 2px 0 0 transparent}
.avatar-menu [role=menuitem]:hover,.avatar-menu [role=menuitem]:focus{background:var(--neutral);color:var(--fg);text-decoration:none;
box-shadow:inset 2px 0 0 var(--brand)}
.avatar-menu [role=menuitem]:focus-visible{outline-offset:-2px}
.avatar-menu [role=separator],.avatar-menu hr{border:0;border-top:1px solid var(--border);margin:4px 0}

/* ---- shell and sidebar */
/* a second navigation column: a tree type's spaces, then the open space's pages */
.shell.has-subnav{grid-template-columns:240px 260px minmax(0,1fr)}
.subnav{border-right:1px solid var(--border);padding:16px 8px;position:sticky;top:56px;height:calc(100vh - 56px);
overflow-y:auto;background:var(--bg)}
.subnav .sec{font-size:12px;line-height:16px;font-weight:600;color:var(--subtlest);margin:12px 8px 4px;list-style:none}
.subnav summary.sec{cursor:pointer}.subnav summary.sec::-webkit-details-marker{display:none}
.subnav summary.sec::before{content:"\25B8";display:inline-block;width:14px}.subnav details.spaces[open]>summary.sec::before{content:"\25BE"}
.subnav ul{list-style:none;margin:0;padding:0}.subnav ul ul{padding-left:14px}
.subnav a{display:block;padding:4px 8px;border-radius:3px;color:var(--subtle);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.subnav a:hover{background:var(--neutral);color:var(--fg);text-decoration:none}
.subnav a[aria-current=page]{background:var(--selected,var(--neutral));color:var(--selected-fg,var(--brand));font-weight:600}
.subnav a.top{font-weight:500}
.subnav .pagetree details{display:block}.subnav .pagetree details>summary{float:left;width:16px;padding:4px 0;cursor:pointer;
color:var(--subtlest);list-style:none;text-align:center}
.subnav .pagetree details>summary::-webkit-details-marker{display:none}
.subnav .pagetree details>summary::before{content:"\25B8"}.subnav .pagetree details[open]>summary::before{content:"\25BE"}
.subnav .pagetree details>a{margin-left:16px}.subnav .pagetree a.leaf{margin-left:16px}
.subnav .add{display:inline-block;margin:0 8px 8px;font-size:13px}
.shell{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:calc(100vh - 56px)}
.side{background:var(--surface);border-right:1px solid var(--border);padding:16px 8px;position:sticky;top:56px;
height:calc(100vh - 56px);overflow-y:auto}
.side .practice{display:flex;gap:8px;align-items:center;padding:4px 12px 16px}
.side .practice b{display:block}.side .practice span{font-size:12px;line-height:16px;color:var(--subtlest)}
.side .sec{font-size:12px;line-height:16px;font-weight:600;color:var(--subtlest);margin:16px 12px 4px}
.side a.nav,.side .nav a{position:relative;display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:3px;
color:var(--subtle);font-weight:500}
.side a.nav:hover,.side .nav a:hover{background:var(--neutral);color:var(--fg);text-decoration:none}
.side a.nav.on,.side a[aria-current=page]{background:var(--selected-bg);color:var(--selected-fg);
box-shadow:inset 2px 0 0 var(--selected-fg)}
.side a.nav.on:hover,.side a[aria-current=page]:hover{background:var(--selected-bg);color:var(--selected-fg)}
.side a svg{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.side .count{margin-left:auto;font-size:12px;line-height:16px;background:var(--neutral);color:var(--fg);border-radius:8px;
padding:0 8px;min-width:24px;text-align:center}
.side a[aria-current=page] .count,.side a.nav.on .count{background:var(--brand);color:var(--brand-fg)}
.side .count.urgent,.side a.nav.on .count.urgent,.side a[aria-current=page] .count.urgent{background:var(--danger);color:var(--danger-on);font-weight:700}
.side .count.also{margin-left:4px}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
/* the Urgent band: first on Your work and on a chart, at every width */
.urgent-band{border:1px solid var(--danger);border-left-width:4px;border-radius:4px;background:var(--danger-bg);padding:8px 16px;margin:0 0 16px}
.urgent-band h2{margin:4px 0 8px;color:var(--danger-fg);display:flex;align-items:center;gap:8px}
.urgent-band .u-row{padding:8px 0;border-top:1px solid var(--border)}
.urgent-band .u-row:first-child{border-top:0}
.urgent-band .u-head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.urgent-band .u-head form{margin-left:auto}
.urgent-band .u-value{color:var(--fg)}
.urgent-band .u-work{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;margin-top:4px}
.urgent-band .u-go{margin-left:auto}
.lane.urgent{color:var(--danger-fg)}
.run-now{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}.run-now dt{font-weight:600;color:var(--subtle)}.run-now dd{margin:0}
details.cancel-work>summary{list-style:none;cursor:pointer}details.cancel-work>summary::-webkit-details-marker{display:none}details.cancel-work[open]>summary{margin-bottom:8px}
.side .legal{margin:24px 12px 0;font-size:12px;line-height:16px;color:var(--subtlest)}
main{padding:24px 32px 32px;min-width:0}main:focus{outline:none}
/* ---- the admin shell: the settings, a screen of their own, with a ribbon of every setup item */
.admin-top .logo{padding:4px 8px}
.admin-top .back-to-work{margin-right:4px;white-space:nowrap}
.admin-shell{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:calc(100vh - 56px)}
.ribbon{background:var(--sunken);border-right:1px solid var(--border);padding:16px 8px;position:sticky;top:56px;
height:calc(100vh - 56px);overflow-y:auto}
.ribbon .rib-sec{font-size:11px;line-height:16px;font-weight:700;font-variant:small-caps;text-transform:lowercase;
letter-spacing:.06em;color:var(--subtlest);margin:16px 12px 4px}
.ribbon a.rib{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:3px;color:var(--subtle);font-weight:500;
box-shadow:inset 3px 0 0 transparent}
.ribbon a.rib:hover{background:var(--neutral);color:var(--fg);text-decoration:none}
.ribbon a.rib.on{background:var(--selected-bg);color:var(--selected-fg);box-shadow:inset 3px 0 0 var(--brand)}
.ribbon a svg,.admin-cards svg{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;
stroke-linecap:round;stroke-linejoin:round}
.admin-group{margin:0 0 24px}.admin-group h2{font-size:16px;line-height:20px;margin:0 0 2px}
.admin-group>p{margin:0 0 8px}
ul.admin-cards{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr));gap:8px}
ul.admin-cards a{display:grid;grid-template-columns:16px minmax(0,1fr);column-gap:8px;row-gap:2px;align-items:center;align-content:start;
padding:12px;border-radius:3px;border:1px solid var(--border);background:var(--surface);color:var(--fg);text-decoration:none;
box-shadow:var(--shadow-raised);height:100%}
ul.admin-cards a svg{color:var(--brand)}ul.admin-cards a .muted{grid-column:2;font-size:12px;line-height:16px}
ul.admin-cards a:hover,ul.admin-cards a:focus-visible{background:var(--raised-hover);border-color:var(--brand)}
@media (max-width:767px){
  .admin-shell{grid-template-columns:minmax(0,1fr);grid-template-rows:auto 1fr}
  .ribbon{position:sticky;top:56px;z-index:10;height:auto;display:flex;align-items:center;gap:4px;padding:8px 16px;
  overflow-x:auto;overflow-y:hidden;border-right:0;border-bottom:1px solid var(--border);scrollbar-width:thin}
  .ribbon .rib-group{display:flex;gap:4px;padding-left:4px;margin-left:4px;border-left:1px solid var(--border)}
  .ribbon .rib-sec{display:none}
  .ribbon a.rib{white-space:nowrap;padding:6px 10px;box-shadow:inset 0 -3px 0 transparent}
  .ribbon a.rib.on{box-shadow:inset 0 -3px 0 var(--brand)}
  .admin-top .logo span:not(.mark){max-width:40vw;overflow:hidden;text-overflow:ellipsis}
}
@media (max-width:480px){.admin-top .logo span:not(.mark){display:none}.admin-top .me .name{display:none}}
.skip{position:absolute;left:8px;top:-48px;z-index:50;background:var(--surface,#fff);padding:8px 12px;border-radius:3px}
.skip:focus{top:8px}

/* ---- breadcrumbs: <nav aria-label="Breadcrumbs" class="crumbs"><ol><li><a>..</a></li>..</ol></nav> */
.crumbs{font-size:14px;line-height:20px;color:var(--subtle);margin:0 0 8px}
.crumbs ol{display:flex;flex-wrap:wrap;list-style:none;margin:0;padding:0}
.crumbs li{display:inline-flex;align-items:center;min-width:0}
.crumbs li+li::before{content:"/";content:"/" / "";padding:0 8px;color:var(--subtlest)}
.crumbs a{color:var(--subtle)}.crumbs a:hover{color:var(--subtle);text-decoration:underline}
.crumbs a[aria-current=page]{color:var(--subtle)}
.crumbs span.sep{margin:0 8px;color:var(--subtlest)}
.pagehead{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px 16px}
.pagehead h1{flex:1;min-width:min(420px,100%)}
.pagehead .actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
#proposed-profile .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
#proposed-profile td{overflow-wrap:anywhere}#proposed-profile tbody th{vertical-align:top}
@media (max-width:640px){main .tablewrap table.fates,main #proposed-profile table{white-space:normal}main #proposed-profile table{display:table}table.fates thead{display:none}table.fates,table.fates tbody,table.fates tr,table.fates td{display:block}table.fates td{border:0;padding:4px 0}table.fates tr{padding:8px 0;border-bottom:1px solid var(--border)}table.fates select{width:100%}}

/* ---- avatars: people round, agents hexagons, services square */
.av{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;font-size:12px;line-height:16px;
font-weight:600;color:#FFFFFF;flex:none;vertical-align:middle;overflow:hidden}
.av.l{width:32px;height:32px}
.av.agent{border-radius:0;clip-path:polygon(25% 3%,75% 3%,100% 50%,75% 97%,25% 97%,0 50%)}
.av.service,.av.automation{border-radius:3px;background:var(--ki-automation)!important;color:var(--ki-fg)}
.av.empty{background:transparent!important;border:2px dashed var(--border-bold);color:var(--subtlest)}
.who{display:inline-flex;align-items:center;gap:8px;vertical-align:middle}

/* ---- badges (labels for kinds and versions) */
.badge{display:inline-block;font-size:12px;line-height:16px;padding:0 4px;border-radius:3px;background:var(--neutral);
color:var(--fg);vertical-align:middle;white-space:nowrap;border:0}
.badge.agent,.badge.proposed,.badge.rt-prompt{background:var(--discovery-bg);color:var(--discovery-fg)}
.badge.person,.badge.signed,.badge.active{background:var(--success-bg);color:var(--success-fg)}
.badge.service,.badge.automation,.badge.rt-python,.badge.rt-external{background:var(--service-bg);color:var(--service)}
.badge.rt-external{background:var(--neutral);color:var(--subtle)}
.badge.urgent,.badge.overdue{background:var(--danger);color:var(--danger-on);font-weight:700}
.badge.due{background:var(--neutral);color:var(--subtle)}
.tile.urgent{box-shadow:inset 3px 0 0 var(--danger)}
/* ---- lozenges: .loz + default|inprogress|success|removed|moved|new, add .bold for the solid look */
.loz,.flag,.risk-low,.risk-medium,.risk-high,.flag-normal,.flag-abnormal,.flag-critical{display:inline-block;max-width:100%;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;padding:0 4px;border:0;border-radius:3px;
font-size:12px;line-height:16px;font-weight:700;text-transform:uppercase;background:var(--loz-default-bg);color:var(--loz-default-fg)}
.loz.inprogress,.loz.prog{background:var(--loz-inprogress-bg);color:var(--loz-inprogress-fg)}
.loz.success,.loz.done{background:var(--loz-success-bg);color:var(--loz-success-fg)}
.loz.removed,.loz.bad{background:var(--loz-removed-bg);color:var(--loz-removed-fg)}
.loz.moved,.loz.wait{background:var(--loz-moved-bg);color:var(--loz-moved-fg)}
.loz.new{background:var(--loz-new-bg);color:var(--loz-new-fg)}
.loz.bold,.loz.bold.default{background:var(--lozb-default-bg);color:var(--lozb-default-fg)}
.loz.bold.inprogress,.loz.bold.prog{background:var(--lozb-inprogress-bg);color:var(--lozb-inprogress-fg)}
.loz.bold.success,.loz.bold.done{background:var(--lozb-success-bg);color:var(--lozb-success-fg)}
.loz.bold.removed,.loz.bold.bad{background:var(--lozb-removed-bg);color:var(--lozb-removed-fg)}
.loz.bold.moved,.loz.bold.wait{background:var(--lozb-moved-bg);color:var(--lozb-moved-fg)}
.loz.bold.new{background:var(--lozb-new-bg);color:var(--lozb-new-fg)}
/* risk and flags: the word is always printed, colour only repeats it */
.risk-low{background:var(--loz-default-bg);color:var(--loz-default-fg)}
.risk-medium{background:var(--loz-moved-bg);color:var(--loz-moved-fg)}
.risk-high{background:var(--lozb-removed-bg);color:var(--lozb-removed-fg)}
.flag-normal{background:var(--loz-default-bg);color:var(--loz-default-fg)}
.flag-abnormal{background:var(--loz-removed-bg);color:var(--loz-removed-fg);box-shadow:inset 0 0 0 1px var(--danger)}
.flag-critical{background:var(--danger);color:var(--danger-on)}

.ki{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:3px;font-size:12px;line-height:16px;
font-weight:700;color:var(--ki-fg);flex:none;vertical-align:middle}
.ki.task{background:var(--ki-task)}.ki.gate{background:var(--ki-gate)}.ki.notice{background:var(--ki-notice)}
.ki.hold{background:var(--ki-hold)}.ki.automation{background:var(--ki-automation)}
.key{font-size:12px;line-height:16px;color:var(--subtle);font-weight:600;white-space:nowrap}

/* ---- section messages: .flash success|error|info|warning (role=status), .section-msg ..., and the older .ok/.error/.warn */
.flash,.section-msg,.error,.ok,.warn{position:relative;padding:16px 16px 16px 48px;border-radius:3px;margin:0 0 16px;
background:var(--info-bg);color:var(--fg)}
.flash::before,.section-msg::before,.error::before,.ok::before,.warn::before{position:absolute;left:16px;top:16px;
width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font:700 12px/20px system-ui,sans-serif;
content:"i";content:"i" / "";background:var(--info-icon);color:var(--surface)}
.flash.success,.section-msg.success,.ok{background:var(--success-bg)}
.flash.success::before,.section-msg.success::before,.ok::before{content:"\2713";content:"\2713" / "";background:var(--success-icon)}
.flash.error,.section-msg.error,.error{background:var(--danger-bg)}
.flash.error::before,.section-msg.error::before,.error::before{content:"!";content:"!" / "";background:var(--danger-icon)}
.section-msg.warning,.flash.warning,.warn{background:var(--warning-bg)}
.section-msg.warning::before,.flash.warning::before,.warn::before{content:"!";content:"!" / "";background:var(--warning-icon);
border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%);padding-top:4px}
.flash>:first-child,.section-msg>:first-child,.error>:first-child,.ok>:first-child,.warn>:first-child{margin-top:0}
.flash>:last-child,.section-msg>:last-child,.error>:last-child,.ok>:last-child,.warn>:last-child{margin-bottom:0}
.flash h2,.flash h3,.section-msg h2,.section-msg h3{margin:0 0 8px;font-size:14px;line-height:20px}
.flash ul,.section-msg ul,.error ul{margin:4px 0;padding-left:16px}
/* ---- a restriction: what is withheld at the subject's request, and the emergency override */
details.break-glass{margin-top:8px}details.break-glass>summary{cursor:pointer;font-weight:600;color:var(--link)}
.break-glass-form{margin-top:8px;max-width:560px}.break-glass-form textarea{width:100%;box-sizing:border-box}
.withheld-panel{border-left:4px solid var(--warning-icon)}tr.withheld td{color:var(--muted)}
.note{font-size:14px;color:var(--danger-fg)}
nav.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:2px solid var(--border);margin:0 0 16px}nav.tabs a{padding:6px 10px;color:var(--subtle);text-decoration:none;margin-bottom:-2px;border-bottom:2px solid transparent}nav.tabs a:hover{color:var(--fg)}nav.tabs a[aria-current]{color:var(--selected-fg);border-bottom-color:currentColor;font-weight:600}.issue-side h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle);margin:16px 0 4px}.sign{font-size:14px}

/* ---- tables (wrap wide ones in <div class="tablewrap">) */
.tablewrap{overflow-x:auto;position:relative;max-width:100%;margin:0 0 16px}
.tablewrap>table{margin:0}
table{width:100%;border-collapse:collapse;border:0;background:transparent;margin:0 0 16px}
th,td{text-align:left;padding:8px;vertical-align:top}
th{font-size:12px;line-height:16px;color:var(--subtle);font-weight:600;border-bottom:2px solid var(--border);vertical-align:bottom}
tbody tr:hover>td,table tr:hover>td{background:var(--neutral)}
table.kv{width:auto;min-width:50%;margin:0}
.prose{max-width:760px;font-size:16px;line-height:1.6;margin:16px 0}
.prose h2,.prose h3,.prose h4,.prose h5{margin:24px 0 8px;line-height:1.3;scroll-margin-top:64px}
.prose h2{font-size:20px}.prose h3{font-size:17px}.prose h4,.prose h5{font-size:15px}
.prose p,.prose ul,.prose ol,.prose blockquote,.prose pre{margin:0 0 12px}
.prose ul,.prose ol{padding-left:24px}
.prose blockquote{border-left:3px solid var(--border-bold);padding:2px 0 2px 12px;color:var(--subtle)}
.prose pre{background:var(--sunken);border-radius:4px;padding:8px 12px;overflow-x:auto;font-size:13px}
.prose code{background:var(--sunken);border-radius:3px;padding:0 3px;font-size:.9em}
.prose pre code{background:none;padding:0}
.prose hr{border:0;border-top:1px solid var(--border);margin:20px 0}
.prose table{margin:0 0 12px}
a.mention{text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}
.mention.missing{color:var(--danger-fg);text-decoration:underline dashed;text-underline-offset:3px;}
h2.prose-label{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle);margin:20px 0 0}
details.editor{margin:8px 0 16px}details.editor>summary{list-style:none;display:inline-block}
details.editor>summary::-webkit-details-marker{display:none}details.editor[open]>summary{margin-bottom:8px}
/* formatted-text editor: a toolbar over an editable page; the Markdown text box under it is the form's value */
.md-field{display:block;margin:12px 0 0}
.md-label{display:block;font-size:12px;line-height:16px;font-weight:600;color:var(--subtle);margin-bottom:4px}
.md-editor{border:2px solid var(--border-input,var(--border));border-radius:3px;background:var(--bg-input,var(--bg))}
.md-editor:focus-within{border-color:var(--brand)}
.md-bar{display:flex;flex-wrap:wrap;align-items:center;gap:2px;padding:4px;border-bottom:1px solid var(--border);
background:var(--surface-sunken,var(--neutral));position:sticky;top:56px;z-index:5}
.md-bar select.md-style{width:auto;margin:0;height:32px;padding:0 8px}
.md-btn,.md-mode{background:transparent;color:var(--fg);border:0;min-width:32px;height:32px;padding:0 8px;border-radius:3px;
font-weight:600;font-size:13px}
.md-btn:hover,.md-mode:hover{background:var(--neutral-hover)}
.md-btn[aria-pressed=true],.md-mode[aria-pressed=true]{background:var(--selected,var(--neutral));color:var(--brand)}
.md-btn:disabled{opacity:.4;cursor:not-allowed}
.md-btn[data-act=I]{font-style:italic}.md-btn[data-act=S]{text-decoration:line-through}
.md-sep{width:1px;height:20px;background:var(--border);margin:0 4px}.md-grow{flex:1}
.md-ask{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px;border-bottom:1px solid var(--border)}
.md-ask[hidden]{display:none}.md-ask-label{font-size:12px;color:var(--subtle)}.md-ask-in{flex:1;min-width:12em;margin:0}
.md-ask-hint{flex-basis:100%;font-size:12px;color:var(--danger-fg)}.md-ask-hint:empty{display:none}
.md-surface{min-height:16em;max-height:70vh;overflow:auto;padding:8px 12px;outline:none}
.md-surface .mention{cursor:default;user-select:all;border-radius:3px;padding:0 2px;background:var(--neutral);color:var(--brand)}
.md-surface table{border-collapse:collapse}.md-surface td,.md-surface th{border:1px solid var(--border);padding:4px 8px;min-width:4em}
.md-surface.md-invalid{box-shadow:inset 0 0 0 2px var(--danger-fg)}
.md-editor textarea.md-source{border:0;border-radius:0;margin:0;width:100%;display:block}
textarea.markdown{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;min-height:16em}
/* a page (layout = "page"): one bar of what it is and what can be done, then its text, read or edited in place */
.page-bar{display:flex;flex-direction:column;gap:8px;padding:0 0 12px;margin:0 0 16px;border-bottom:1px solid var(--border)}
.page-meta{display:flex;flex-wrap:wrap;align-items:center;gap:4px 16px;font-size:12px;line-height:20px;color:var(--subtle)}
.page-meta .k{font-weight:600;margin-right:2px}.page-meta a.meta-item{color:var(--subtle)}
.page-meta ul,.page-meta p{display:inline;padding:0;margin:0;list-style:none}.page-meta li{display:inline}
.page-meta li+li::before{content:", "}
.page-controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.page-controls .grow{flex:1}
.page-controls nav.tabs{margin:0;border-bottom:0}.page-controls form.inline{margin:0}
.page-body{max-width:780px}
.lozenge.editing-flag{background:var(--brand);color:#fff}
details.menu-pop{position:relative}details.menu-pop>summary{list-style:none}
details.menu-pop>summary::-webkit-details-marker{display:none}
details.menu-pop>ul{position:absolute;right:0;top:calc(100% + 4px);z-index:20;min-width:240px;max-height:60vh;overflow:auto;
margin:0;padding:8px 12px;background:var(--raised);border-radius:3px;box-shadow:var(--shadow-overlay,var(--shadow-raised))}
.page-edit{max-width:780px}
.page-edit .page-bar{position:sticky;top:56px;z-index:7;background:var(--bg);padding-top:8px}
.page-title-field label{display:block;font-size:12px;color:var(--subtle)}
.page-title-field input{font-size:24px;line-height:32px;font-weight:500;height:auto;padding:4px 8px;margin-top:2px}
.page-details{margin:8px 0}.page-details>summary{cursor:pointer;color:var(--subtle);font-size:13px}
.page-details-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0 16px}
.page-text .md-label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.page-text .md-editor{border:0;background:transparent}
.page-text .md-bar{top:var(--page-bar-bottom,150px);border:1px solid var(--border);border-radius:3px}
.page-text .md-surface{max-height:none;min-height:60vh;padding:16px 0}
.page-text .md-help{display:block;margin-top:8px}
/* a board: a card is dragged to a column it may move to, or moved with its Move to… list (a plain form) */
.tile.movable{padding:0;-webkit-touch-callout:none}.tile.movable[draggable=true]{cursor:grab}
.tile.movable .tile-link{display:block;padding:12px 12px 4px;color:inherit}
.tile.movable .tile-link:hover{text-decoration:none}
.tile .move-to{display:flex;gap:4px;padding:4px 12px 12px;margin:0}
.tile .move-to select{flex:1;min-width:0;width:auto;margin:0;height:32px;font-size:12px}
.tile .move-to button{flex:none;font-size:12px;padding:0 10px}
.tile.dragging{opacity:.5}.tile.pending{opacity:.6;pointer-events:none;outline:2px dashed var(--border);outline-offset:-2px}
.board .col{transition:opacity .1s}
.board .col.drop-ok{outline:2px dashed var(--brand);outline-offset:-2px}
.board .col.drop-over{background:var(--selected-bg);outline-style:solid}
.board .col.drop-no{opacity:.45;cursor:no-drop}
.drag-ghost{position:fixed;z-index:100;pointer-events:none;margin:0;opacity:.9;box-shadow:var(--shadow-overlay);transform:rotate(2deg)}
.touch-dragging,.touch-dragging *{user-select:none;-webkit-user-select:none}
@media (prefers-reduced-motion:reduce){.board .col{transition:none}.drag-ghost{transform:none}}
/* a form in a dialog over the page (Create, and a move that asks for fields) */
dialog.form-modal{width:min(720px,calc(100vw - 32px));max-height:calc(100vh - 64px);padding:0;border:0;border-radius:3px;
background:var(--raised);color:var(--fg);box-shadow:var(--shadow-overlay,var(--shadow-raised))}
dialog.form-modal::backdrop{background:rgba(9,30,66,.54)}
dialog.form-modal .fm-head{display:flex;align-items:center;gap:8px;padding:20px 24px 8px}
dialog.form-modal .fm-head h2{margin:0;font-size:20px;flex:1}
dialog.form-modal .fm-body{padding:0 24px;overflow:auto;max-height:calc(100vh - 200px)}
dialog.form-modal .fm-body form.card{box-shadow:none;padding:0;background:transparent}
dialog.form-modal .fm-close{background:transparent;border:0;color:var(--fg);font-size:20px;min-width:32px;height:32px}
dialog.form-modal .fm-close:hover{background:var(--neutral-hover)}
dialog.form-modal .fm-close:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
/* a list to choose from: what to create, what work to start */
ul.pick{list-style:none;margin:8px 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(320px,100%),1fr));gap:8px}
ul.pick a{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:3px;border:1px solid var(--border);color:var(--fg);text-decoration:none}
ul.pick a:hover,ul.pick a:focus-visible{background:var(--neutral);border-color:var(--brand)}
ul.pick .muted{font-size:12px}
header.top .btn.quick{background:var(--neutral);border:0;color:var(--fg);margin-right:4px;white-space:nowrap}
header.top .btn.quick:hover{background:var(--neutral-hover);color:var(--fg)}
header.top .btn.subtle{margin-right:4px;white-space:nowrap}
@media (max-width:1100px){header.top .btn.quick{display:none}}
ul.tree{list-style:none;padding:0;margin:0}
ul.tree li{padding:4px 0 4px calc(var(--depth,0) * 20px);border-bottom:1px solid var(--border)}
ul.tree li::before{content:"";display:inline-block;width:6px;height:6px;margin-right:8px;border-radius:1px;
  background:var(--border-bold);vertical-align:middle}
ul.toc li.l2{padding-left:12px}ul.toc li.l3,ul.toc li.l4{padding-left:24px}
section.children h2{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
section.hand-links h3{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:12px 0 4px}
section.hand-links li{display:flex;gap:8px;align-items:center}section.hand-links form.inline{margin:0;display:inline}
table.kv th{width:1%;white-space:nowrap;padding:4px 12px 4px 0;border:0;vertical-align:top;font-size:14px;line-height:20px;
font-weight:400;color:var(--subtlest)}
table.kv td{padding:4px 12px 4px 0;color:var(--fg);font-weight:500}
table.kv tr:hover>td{background:transparent}

/* ---- cards, sections, subject file */
.card{background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:16px;margin:0 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px 16px}
.section{margin:24px 0}.section>h2{margin-top:0}
.subject-banner{display:flex;flex-wrap:wrap;align-items:center;gap:8px 24px;padding:16px;margin:0 0 16px;
background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised)}
.subject-banner .name{font-size:20px;line-height:24px;font-weight:600;margin:0;color:var(--fg)}
.subject-banner .facts{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0;padding:0;list-style:none}
.subject-banner .facts>*{display:inline-flex;gap:4px;align-items:baseline;margin:0}
.subject-banner .facts dt,.subject-banner .facts .lbl,.subject-banner .facts .label{color:var(--subtlest);font-size:12px;
line-height:16px;font-weight:400;margin:0}
.subject-banner .facts dd{margin:0;color:var(--fg);font-weight:500}
.alert-banner{display:flex;align-items:flex-start;gap:8px;padding:12px 16px;margin:0 0 16px;border-radius:3px;
background:var(--danger);color:var(--danger-on);font-weight:700}
.alert-banner::before{content:"!";content:"!" / "";flex:none;display:grid;place-items:center;width:20px;height:20px;
border-radius:3px;background:var(--danger-on);color:var(--danger);font-weight:700}
.alert-banner a{color:inherit;text-decoration:underline}
.chart-section{background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);margin:0 0 16px}
.chart-section>header{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border)}
.chart-section>header h2,.chart-section>header h3{margin:0;flex:1}
.chart-section>.body{padding:12px 16px}
.chart-section>table,.chart-section>.tablewrap{margin:0}
.chart-section>.tablewrap{padding:0 8px 8px}.chart-section>table{width:calc(100% - 16px);margin:0 8px 8px}
.chart-section>.empty,.chart-section>p{margin:0}
.chart-section>p{padding:12px 16px}
.chart-section{scroll-margin-top:72px}
.chart-section>header .btn{flex:none}
.count-badge{display:inline-block;vertical-align:middle;margin-left:4px;font-size:12px;line-height:16px;font-weight:600;
padding:0 8px;border-radius:8px;background:var(--neutral);color:var(--fg)}
table.parts{width:100%}table.parts td.t>a{font-weight:500}
table.parts td.t{width:100%;overflow-wrap:anywhere}table.parts th:not(:first-child){white-space:nowrap}
.chart-section.empty-part>header{border-bottom:0}.chart-section>header .none{flex:none;font-size:14px}
@media (max-width:600px){table.parts,table.parts tbody{display:block}table.parts tr:first-child{display:none}
table.parts tr{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)}
table.parts td{display:block;border:0;padding:0}table.parts td.t{flex:1 1 100%;white-space:normal}
table.parts td:empty{display:none}.chart-section>header{flex-wrap:wrap}}
table.parts td.nowrap{white-space:nowrap}
details.peek{display:inline}details.peek>summary{display:inline-block;list-style:none;cursor:pointer;margin-left:6px;
padding:0 4px;border-radius:3px;color:var(--subtlest);font-size:12px}
details.peek>summary::-webkit-details-marker{display:none}
details.peek>summary::before{content:"\25B8"}details.peek[open]>summary::before{content:"\25BE"}
details.peek>summary:hover{background:var(--neutral-hover);color:var(--fg)}
details.peek[open]>:not(summary){display:block;margin:8px 0 4px;padding:8px 12px;border-left:2px solid var(--border);
font-weight:400;white-space:normal}
h2.links-head{margin:32px 0 12px}.issue .chart-section{margin-top:16px}
.owner-rail a{display:flex;align-items:center;gap:8px}
.owner-rail .count{margin-left:auto;font-size:12px;line-height:16px;background:var(--neutral);color:var(--fg);border-radius:8px;
padding:0 8px;min-width:24px;text-align:center}
.part-of{margin:0 0 12px;color:var(--subtle)}.part-of a{font-weight:600}
.subject-banner h1.name{margin:0}
.subject-banner+.alert-banner{margin-top:-8px}
.empty:not(.av){padding:24px;text-align:center;color:var(--subtlest)}
.empty-state{padding:12px 16px;text-align:left;color:var(--subtlest);border:2px dashed var(--border);border-radius:3px}

/* ---- forms */
label{display:block;margin:12px 0 0;font-weight:600;font-size:12px;line-height:16px;color:var(--subtle)}
label small,.field>small.help{display:block;font-weight:400;font-size:12px;line-height:16px;color:var(--subtlest);margin-top:4px}
/* a control with help: the label holds the words and the control, the help sits below it (aria-describedby) */
.field{margin:12px 0 0;min-width:0}.field>label{margin-top:0}
form.stack>.field,form.card:not(.grid)>.field,details>form:not(.inline)>.field,form.grid>.field{margin:0}
.field[hidden]{display:none}
textarea.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
input,select,textarea{display:block;width:100%;min-height:32px;margin-top:4px;padding:4px 8px;border:2px solid var(--input-border);
border-radius:3px;background:var(--input-bg);color:var(--fg);font:inherit;font-size:14px;line-height:20px;font-weight:400}
input:hover,select:hover,textarea:hover{background:var(--input-hover)}
input:focus,select:focus,textarea:focus{background:var(--input-hover);border-color:var(--focus);box-shadow:inset 0 0 0 1px var(--focus)}
/* the border is the focus ring for text controls; a transparent outline keeps it visible in forced-colours mode */
input:not([type=checkbox]):not([type=radio]):focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid transparent}
input:disabled,select:disabled,textarea:disabled{background:var(--disabled-bg);color:var(--disabled-fg);cursor:not-allowed;border-style:dashed}
input[aria-invalid=true],select[aria-invalid=true],textarea[aria-invalid=true]{border-color:var(--danger)}
input[type=checkbox],input[type=radio]{display:inline-block;width:16px;height:16px;min-height:0;margin:0 8px 0 0;padding:0;
vertical-align:middle;accent-color:var(--brand);box-shadow:none}
input[type=hidden]{display:none}
label.radio{font-weight:400;color:var(--fg);display:inline-flex;align-items:center;margin:4px 16px 4px 0;font-size:14px;line-height:20px}
textarea.code{font:13px/20px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
fieldset.choices{border:0;margin:12px 0 0;padding:0;min-width:0}
fieldset.slot-picker{border:0;margin:12px 0 0;padding:0;min-width:0}
fieldset.slot-picker>legend{padding:0;margin:0 0 4px;font-size:12px;line-height:16px;font-weight:600;color:var(--subtle)}
.slot-filters{display:flex;flex-wrap:wrap;gap:0 12px}.slot-filters label{flex:1 1 160px;margin-top:0}
/* a screen's tabs: every part stays in the form (a required field is never hidden); the tab links jump to a part */
fieldset.tabpanel{border:0;border-top:1px solid var(--border);margin:16px 0 0;padding:8px 0 0;min-width:0;scroll-margin-top:64px}
fieldset.tabpanel:first-of-type{border-top:0;margin-top:0}
fieldset.tabpanel>legend{font-size:14px;line-height:20px;font-weight:600;color:var(--fg);padding:0 8px 0 0}
fieldset.tabpanel:target>legend{color:var(--selected-fg)}
form.tabbed fieldset.tabpanel{border-top:0;margin-top:0;padding-top:0}
form.tabbed fieldset.tabpanel>legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
form.tabbed nav.form-tabs a[aria-selected=true]{color:var(--selected-fg);border-bottom-color:currentColor;font-weight:600}
.tab-next{margin-top:12px}
table.kv th.group{padding-top:16px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle)}
fieldset.choices legend{padding:0;margin:0 0 4px;font-size:12px;line-height:16px;font-weight:600;color:var(--subtle)}
fieldset.choices label{display:flex;align-items:center;margin:4px 0;font-weight:400;font-size:14px;line-height:20px;color:var(--fg)}
fieldset.choices.inline label{display:inline-flex;margin-right:16px}
/* a single choice is a row of option cards, several are chips: the native input stays (keyboard, screen
   readers, required, posting) but is hidden; the card around it shows the choice */
fieldset.options{display:flex;flex-wrap:wrap;gap:8px}
fieldset.options>legend{width:100%;float:left}
input.opt-input{position:absolute;opacity:0;width:1px;height:1px;margin:0;pointer-events:none}
fieldset.options label.radio,label.radio:has(>input.opt-input){position:relative;display:inline-flex;flex-direction:column;
align-items:flex-start;justify-content:center;margin:0;padding:8px 14px;border:1px solid var(--border);border-radius:8px;
background:var(--surface);cursor:pointer;font-weight:500;color:var(--fg);font-size:14px;line-height:20px}
label.radio:has(>input.opt-input):hover{background:var(--hover)}
label.radio:has(>input.opt-input:checked){border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand);
background:var(--selected-bg);color:var(--selected-fg)}
label.radio:has(>input.opt-input:focus-visible){outline:2px solid var(--brand);outline-offset:2px}
label.radio:has(>input.opt-input:disabled){opacity:.6;cursor:not-allowed}
label.radio>small{display:block;color:var(--subtle);font-weight:400;font-size:12px;line-height:16px}
label.radio:has(>input.opt-input:checked)>small{color:var(--subtle)}
fieldset.options.decide label.radio{min-width:160px}
fieldset.options.decide label.radio>b{font-weight:600}
/* a yes/no tick is a switch: the native checkbox, drawn as a track and knob */
label.switch-row{display:inline-flex;align-items:center;gap:8px;font-weight:400;cursor:pointer;align-self:flex-start}
input.switch{appearance:none;-webkit-appearance:none;position:relative;flex:none;width:32px;height:18px;margin:0;
border-radius:9px;background:var(--border-bold);cursor:pointer;transition:background .15s}
input.switch::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;
background:var(--surface);transition:transform .15s}
input.switch:checked{background:var(--brand)}
input.switch:checked::after{transform:translateX(14px)}
input.switch:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
@media (prefers-reduced-motion:reduce){input.switch,input.switch::after{transition:none}}
/* stacked forms: the gap spaces fields and buttons, buttons sit at the start */
form.card:not(.grid),details>form:not(.inline),form.stack{display:flex;flex-direction:column;align-items:stretch;gap:12px}
form.card:not(.grid)>label,details>form:not(.inline)>label,form.stack>label,
form.card:not(.grid)>fieldset,details>form:not(.inline)>fieldset,form.stack>fieldset,
form.card:not(.grid)>p,details>form:not(.inline)>p,form.stack>p{margin:0}
form.card:not(.grid)>label.radio,details>form:not(.inline)>label.radio,form.stack>label.radio{align-self:flex-start}
form.card:not(.grid)>:is(button,.btn,.actions),details>form:not(.inline)>:is(button,.btn,.actions),form.stack>:is(button,.btn,.actions){align-self:flex-start}
form.grid>label{margin:0}form.grid>:is(button,.btn){align-self:end;justify-self:start}
form .actions,.form-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
form.inline{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0}
form.inline input:not([type=checkbox]):not([type=radio]),form.inline select{width:auto;flex:1;min-width:140px;margin:0}
form.inline label{margin:0}
/* connections (MCP): buttons in a row; the server table stacks into labelled rows on a phone */
.mcp-btns{display:flex;flex-wrap:wrap;gap:8px}
@media (max-width:640px){table.connections thead{display:none}table.connections tr{display:block;padding:8px 0;
border-bottom:1px solid var(--border)}table.connections td{display:block;border:0;padding:4px 8px;text-align:left;white-space:normal;overflow-wrap:anywhere}
table.connections td[data-label]:not(:first-child)::before{content:attr(data-label) ": ";font-weight:600;color:var(--subtle)}
table.connections code.url{word-break:break-all}}

/* ---- buttons: default (neutral), .primary, .subtle, .danger; .secondary is the default */
button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:4px;height:32px;padding:0 12px;margin:0;
border:0;border-radius:3px;background:var(--neutral);color:var(--subtle);font:inherit;font-size:14px;line-height:20px;
font-weight:500;cursor:pointer;text-decoration:none;white-space:nowrap;vertical-align:middle;max-width:100%}
button:hover,.btn:hover{background:var(--neutral-hover);color:var(--subtle);text-decoration:none}
button:active,.btn:active{background:var(--neutral-pressed)}
button.secondary,.btn.secondary{background:var(--neutral);color:var(--subtle)}
button.secondary:hover,.btn.secondary:hover{background:var(--neutral-hover);color:var(--subtle)}
button.primary,.btn.primary,button.create,.btn.create{background:var(--brand);color:var(--brand-fg)}
button.primary:hover,.btn.primary:hover,button.create:hover,.btn.create:hover{background:var(--brand-hover);color:var(--brand-fg)}
button.primary:active,.btn.primary:active,button.create:active,.btn.create:active{background:var(--brand-pressed)}
button.subtle,.btn.subtle{background:transparent;color:var(--subtle)}
button.subtle:hover,.btn.subtle:hover{background:var(--neutral-hover);color:var(--subtle)}
button.danger,.btn.danger{background:var(--danger);color:var(--danger-on)}
button.danger:hover,.btn.danger:hover{background:var(--danger-hover);color:var(--danger-on)}
button.danger-subtle,.btn.danger-subtle{background:transparent;color:var(--danger-fg);box-shadow:inset 0 0 0 1px var(--danger)}
button.danger-subtle:hover,.btn.danger-subtle:hover{background:var(--danger-bg);color:var(--danger-fg)}
button:disabled,.btn[aria-disabled=true]{background:var(--disabled-bg);color:var(--disabled-fg);cursor:not-allowed}
/* neutral buttons carry a hairline edge so they read as controls; filled, subtle and disabled ones do not */
button:not([class]),button.secondary,.btn{box-shadow:inset 0 0 0 1px var(--control-border)}
button.primary,.btn.primary,button.create,.btn.create,button.danger,.btn.danger,button.subtle,.btn.subtle,button.link,
header.top .btn.quick,.avatar-menu>button,.ed-tabs button,button:disabled,.btn[aria-disabled=true]{box-shadow:none}
button.link{background:transparent;color:var(--link);padding:0 4px}
button.link:hover{text-decoration:underline;color:var(--link-hover)}

pre{background:var(--sunken);padding:8px 12px;border-radius:3px;overflow-x:auto;font-size:12px;line-height:16px}
pre.wrap{white-space:pre-wrap}
pre.diff .add{color:var(--success-fg)}pre.diff .del{color:var(--danger-fg)}
ul.plain{list-style:none;padding:0;margin:0}ul.plain li{padding:4px 0}
details summary{cursor:pointer}details.card[open]>summary{margin-bottom:12px}

/* ---- modal dialog (built by the script for data-confirm) */
dialog.modal{border:0;padding:0;border-radius:3px;box-shadow:var(--shadow-overlay);background:var(--overlay);color:var(--fg);
width:min(400px,calc(100vw - 32px));max-height:calc(100vh - 64px)}
dialog.modal::backdrop{background:#091E427A}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) dialog.modal::backdrop{background:#10121499}}
:root[data-theme=dark] dialog.modal::backdrop{background:#10121499}
dialog.modal form{margin:0}
dialog.modal h2{font-size:20px;line-height:24px;margin:0;padding:24px 24px 16px}
dialog.modal .modal-body{padding:0 24px;margin:0;white-space:pre-line}
dialog.modal .modal-actions{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:8px;padding:24px}

/* ---- filters */
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
.starts{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
.starts::before{content:"Start work";flex-basis:100%;font-size:12px;font-weight:600;color:var(--subtle)}
.file-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
form p.about{margin:0;color:var(--subtle)}
.filters input{width:auto;flex:0 1 240px;margin:0}
.chip{display:inline-flex;align-items:center;gap:4px;height:32px;padding:0 12px;border-radius:3px;font-weight:500;
color:var(--subtle);background:var(--neutral)}
.chip:hover{background:var(--neutral-hover);color:var(--subtle);text-decoration:none}
.chip{box-shadow:inset 0 0 0 1px var(--control-border)}
.chip.on,.chip[aria-current],.chip[aria-pressed=true]{background:var(--selected-bg);color:var(--selected-fg);box-shadow:inset 0 0 0 1px var(--selected-fg)}
.filters .gap{width:8px}.filters .lbl{font-size:12px;line-height:16px;color:var(--subtle);font-weight:600}

/* ---- board */
.stat{font-size:32px;line-height:40px;font-weight:600;margin:0}
table.chart th{width:30%;font-weight:400}.bar{display:inline-block;height:12px;border-radius:2px;background:var(--brand);vertical-align:middle}
.board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(208px,1fr);gap:8px;overflow-x:auto;padding-bottom:8px;position:relative;min-width:0}
.col{background:var(--sunken);border-radius:3px;padding:8px;min-height:200px}
.col>header{display:flex;align-items:center;gap:8px;padding:4px 4px 12px;font-size:12px;line-height:16px;font-weight:600;
color:var(--subtle)}.col>header .col-name{font-size:14px;line-height:20px;color:var(--fg)}
.col>header .n{font-weight:600;min-width:20px;padding:0 6px;border-radius:10px;background:var(--neutral);text-align:center}
.lane{margin:16px 0 8px;font-weight:600;display:flex;align-items:center;gap:8px}
.tile{display:block;background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:12px;margin-bottom:8px;color:var(--fg)}
.tile:hover{background:var(--raised-hover);color:var(--fg);text-decoration:none}
.tile .t{font-weight:400;margin-bottom:8px}.tile .about{font-size:12px;line-height:16px;color:var(--subtlest);margin:-4px 0 8px}
.tile .foot{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}.tile .foot .av{margin-left:auto}
.tile .why{font-size:12px;line-height:16px;color:var(--danger-fg);margin:-4px 0 8px}

/* ---- issue layout: content left, side panel (status and actions first) right */
.issue{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:24px;align-items:start}
.issue>*{min-width:0}
.issue-side{display:flex;flex-direction:column;gap:16px;position:sticky;top:72px}
.issue-side .status,.issue-side .side-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.details{background:var(--surface);border:1px solid var(--border);border-radius:3px;padding:4px 16px;position:sticky;top:72px}
.issue-side .details{position:static}
.details dl{display:grid;grid-template-columns:112px minmax(0,1fr);gap:12px 8px;margin:12px 0}
.details dt{color:var(--subtle);font-weight:600}.details dd{margin:0;min-width:0;overflow-wrap:anywhere}
.details h3{margin:12px 0 0;color:var(--fg)}
/* ---- the record's side panel: its lifecycle, then the open work about it (a card per run) */
.issue-side section>h2:first-child{margin-top:0}
.issue-side .count,.wp-inline .count{font-size:12px;line-height:16px;background:var(--neutral);color:var(--fg);border-radius:8px;padding:0 6px;margin-left:4px;letter-spacing:0}
.wf-link{font-size:14px}
.wp-status .side-actions{margin-top:8px}
.wcard{border:1px solid var(--border);border-radius:3px;background:var(--surface);padding:8px 12px;margin:0 0 8px;box-shadow:var(--shadow-raised,none)}
.wcard-head a{font-weight:600}
.wnow{display:grid;grid-template-columns:40px minmax(0,1fr);gap:4px;margin:6px 0;font-size:14px}
.wnow .k{color:var(--subtle);font-size:12px;font-weight:600;padding-top:2px}
.wnow li{margin:0 0 2px;overflow-wrap:anywhere}
.wstrip{display:flex;gap:2px;list-style:none;padding:0;margin:8px 0 4px}
.wstrip .seg{flex:1;height:6px;border-radius:2px;background:var(--neutral);min-width:4px}
.wstrip .seg.done{background:var(--loz-success-fg)}
.wstrip .seg.now{background:var(--brand)}
.wstrip .seg.maybe{background:transparent;border:1px dashed var(--border)}
.wstrip .seg.hidden{background:repeating-linear-gradient(45deg,var(--neutral),var(--neutral) 3px,transparent 3px,transparent 6px);flex:2}
.wsteps summary{font-size:12px;color:var(--subtle);cursor:pointer}
.wsteps ol{margin:4px 0 0;font-size:13px}.wsteps li{padding:2px 0}
.sw{display:inline-block;min-width:64px;font-size:11px;font-weight:700;text-transform:uppercase;color:var(--subtle)}
.sw.done{color:var(--loz-success-fg)}.sw.now{color:var(--selected-fg)}
.wp-mine{display:block;text-align:center;margin-top:8px}
.wp-inline{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px;margin:8px 0 16px}
.wp-inline section{display:contents}.wp-inline section>h2{grid-column:1/-1;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle);margin:0}
.wp-below{margin-top:24px}
/* ---- the workflow editor's graph: states, the automations on the way, ways out */
.wf-canvas{padding:8px;min-height:160px}.wf-canvas svg{margin:0}
.wf-graph .fl-edge.sel{stroke:var(--brand);stroke-width:2.2}.wf-graph .fl-edge.fail{stroke:var(--danger)}
.wf-graph .fl-arrowhead.sel{fill:var(--brand)}.wf-graph .fl-arrowhead.fail{fill:var(--danger)}
.wf-node{cursor:pointer}.wf-node .box{fill:var(--raised);stroke:var(--border-bold);stroke-width:1.4}
.wf-node.k-start .box{fill:var(--neutral);stroke-dasharray:4 3}
.wf-node.k-do .box{fill:var(--surface);stroke:var(--ki-automation);stroke-dasharray:5 3}
.wf-node.sel>.box,.wf-node:focus-visible>.box{stroke:var(--brand);stroke-width:2.4;stroke-dasharray:none}
.wf-node text{fill:var(--fg);font:600 12px system-ui,sans-serif}.wf-node text.s{fill:var(--subtlest);font-size:11px;font-weight:500}
.wf-node text.name{font-size:13px}.wf-node text.w{font-weight:500}
.wf-node .tone{fill:var(--loz-default-bg,var(--neutral))}.wf-node .t-inprogress{fill:var(--loz-inprogress-fg)}
.wf-node .t-moved{fill:var(--loz-moved-fg)}.wf-node .t-success{fill:var(--loz-success-fg)}.wf-node .t-removed{fill:var(--loz-removed-fg)}
.wf-node .hit{fill:transparent}.wf-head:hover .hit,.wf-row:hover .hit{fill:var(--neutral-hover)}
.wf-row.sel .hit{fill:var(--selected-bg);stroke:var(--brand)}.wf-head:focus-visible .hit,.wf-row:focus-visible .hit{stroke:var(--brand);stroke-width:2}
.wf-node :focus,.wf-node:focus{outline:none}
.wf-node .ki-box{fill:var(--ki-automation)}.wf-node .ki-box.k-task{fill:var(--ki-task)}.wf-node .ki-box.k-gate{fill:var(--ki-gate)}
.wf-node text.ki-t{fill:#fff;font-size:10px;font-weight:700}.wf-node text.ai-t2{fill:var(--subtle);font-size:10px;font-weight:700}
.wf-handle{fill:var(--surface);stroke:var(--brand);stroke-width:1.6;cursor:crosshair}.wf-handle:hover{fill:var(--brand)}
.wf-elabel{cursor:pointer}.wf-elabel rect{fill:var(--surface);stroke:var(--border-bold)}
.wf-elabel text{fill:var(--fg);font:600 11px system-ui,sans-serif}.wf-elabel.muted text{fill:var(--subtle);font-weight:500}
.wf-elabel.muted,.wf-elabel.fail{cursor:default}.wf-elabel.fail rect{stroke:var(--danger)}.wf-elabel.fail text{fill:var(--danger)}
.wf-elabel.sel rect,.wf-elabel:focus-visible rect{stroke:var(--brand);stroke-width:2;fill:var(--selected-bg)}.wf-elabel:focus{outline:none}
.wf-drag{pointer-events:none}
/* ---- a lifecycle drawn: states as boxes, moves as arrows */
.wf-states{display:flex;flex-direction:column;gap:12px;margin:8px 0 16px}
.wf-state{border:1px solid var(--border);border-radius:3px;padding:10px 12px;background:var(--surface)}
.wf-state.wf-entry{border-style:dashed;background:transparent}
.wf-state-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px}
.wf-work,.wf-do{list-style:none;margin:8px 0 0;padding:0}.wf-work li,.wf-do li{margin:4px 0;font-size:13px}
.wf-edges{list-style:none;margin:10px 0 0;padding:0 0 0 12px;border-left:2px solid var(--border)}
.wf-edge{margin:8px 0}.wf-edge-head{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.wf-edge-name{font-weight:600;border:1px solid var(--border-bold);border-radius:3px;padding:1px 8px;background:var(--raised)}
.wf-arrow{color:var(--subtle)}.wf-cond{font-size:12px;margin-top:4px}.wf-cond code,.wf-state code{font-size:11px;word-break:break-word}
.wf-edit{margin:0}.wf-edit .wf-state.sel{border-color:var(--brand)}
.wf-pick{font:inherit;color:inherit;background:none;border:1px solid transparent;border-radius:3px;padding:1px 4px;cursor:pointer;text-align:left;line-height:inherit}
.wf-pick:hover{border-color:var(--border-bold)}.wf-pick.sel{border-color:var(--brand);box-shadow:0 0 0 1px var(--brand)}
.wf-pick.wf-edge-name{font-weight:600;border-color:var(--border-bold);background:var(--raised)}
.wf-work{list-style:none;margin:8px 0 0;padding:0;font-size:13px}.wf-work li,.wf-do li{margin:2px 0}
.wf-add button{font-size:12px;padding:0 6px}.wf-says{display:block;color:var(--subtle)}
.wf-do{padding-left:12px}.wf-steps{margin-top:8px}.wf-steps summary{cursor:pointer;color:var(--subtle)}
.lc-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:3px;background:var(--sunken);padding:8px;margin:8px 0}
.lc-svg{display:block;margin:0 auto;width:100%;min-width:600px;height:auto}
.lc-node rect{fill:var(--loz-default-bg);stroke:var(--border);stroke-width:1}
.lc-node text{fill:var(--loz-default-fg);font-size:11px;font-weight:700;text-anchor:middle;letter-spacing:.02em}
.lc-node.success rect{fill:var(--loz-success-bg)}.lc-node.success text{fill:var(--loz-success-fg)}
.lc-node.removed rect{fill:var(--loz-removed-bg)}.lc-node.removed text{fill:var(--loz-removed-fg)}
.lc-node.inprogress rect{fill:var(--loz-inprogress-bg)}.lc-node.inprogress text{fill:var(--loz-inprogress-fg)}
.lc-node.start rect{stroke-width:2}
.lc-node.current rect{stroke:var(--brand);stroke-width:3}
.lc-edge{fill:none;stroke:var(--border-bold);stroke-width:1.5;stroke-linejoin:round}
.lc-edge.auto{stroke-dasharray:5 4}
.lc-edge.here{stroke:var(--brand);stroke-width:2.5}
.lc-head{fill:var(--border-bold)}.lc-head.here{fill:var(--brand)}
.lc-any rect{fill:var(--surface);stroke:var(--border)}.lc-any text{fill:var(--subtle);font-size:10px;text-anchor:middle}
.lc-any.here rect{stroke:var(--brand)}
.lc-key{display:inline-block;width:18px;height:10px;vertical-align:middle;margin:0 4px 0 12px;border-radius:2px}
.lc-key:first-child{margin-left:0}
.lc-key.cur{border:3px solid var(--brand)}.lc-key.here{height:0;border-top:3px solid var(--brand)}
.lc-key.auto{height:0;border-top:2px dashed var(--border-bold)}
.lc-moves tr.here td{background:var(--selected-bg)}
.activity{list-style:none;padding:0;margin:0}.activity li{display:flex;gap:8px;padding:8px 0;border-bottom:1px solid var(--border)}
.activity li:last-child{border-bottom:0}.activity .body{flex:1;min-width:0}
.activity .meta{font-size:12px;line-height:16px;color:var(--subtlest)}
.subtasks{border:1px solid var(--border);border-radius:3px;background:var(--surface)}
.subtasks .row{display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border)}
.subtasks .row:last-child{border-bottom:0}.subtasks .row:hover{background:var(--neutral)}
.subtasks .row .t{flex:1;min-width:0}.subtasks .row.skipped{opacity:.6}

/* ---- workflow diagram */
.flowwrap{background:var(--surface);border:1px solid var(--border);border-radius:3px;overflow:auto;padding:4px}
.flowwrap svg{display:block;margin:0 auto}
.fl-edge{fill:none;stroke:var(--border-bold);stroke-width:1.6}.fl-edge.cond{stroke-dasharray:5 4}
.fl-arrowhead{fill:var(--border-bold)}
.fl-node rect{fill:var(--raised);stroke:var(--border-bold);stroke-width:1.4}
.fl-node .bar{stroke:none}.fl-node.k-task .bar{fill:var(--ki-task)}.fl-node.k-gate .bar{fill:var(--ki-gate)}
.fl-node.k-automation .bar{fill:var(--ki-automation)}
.fl-node text{fill:var(--fg);font:600 12px system-ui,sans-serif}.fl-node text.k{fill:var(--subtlest);font-size:10px;font-weight:700}
.fl-node text.s{fill:var(--subtlest);font-size:11px;font-weight:500}
.fl-node.st-done rect.box{fill:var(--success-bg);stroke:var(--success)}.fl-node.st-active rect.box{fill:var(--selected-bg);stroke:var(--brand)}
.fl-node.st-failed rect.box{fill:var(--danger-bg);stroke:var(--danger)}.fl-node.st-skipped{opacity:.45}
.fl-node.sel rect.box{stroke:var(--brand);stroke-width:2.6}.fl-node.click{cursor:pointer}.fl-node.click:hover rect.box{stroke:var(--brand)}
.fl-node:focus-visible{outline:2px solid var(--focus)}
.fl-node .ai{fill:var(--discovery-bg)}.fl-node text.ai-t{fill:var(--discovery-fg);font-size:9px;font-weight:800}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;line-height:16px;color:var(--subtle);margin:8px 4px}
.legend span{display:inline-flex;align-items:center;gap:4px}
.board-legend{margin:8px 4px;font-size:12px;color:var(--subtle)}.board-legend ul{display:flex;flex-wrap:wrap;gap:6px 16px;margin:6px 0;padding:0}
.board-legend li{display:inline-flex;align-items:center;gap:4px}

/* ---- workflow editor */
.ed-tabs{display:flex;gap:4px;border-bottom:2px solid var(--border);margin-bottom:12px}
.ed-tabs button,.ed-tabs [role=tab]{background:transparent;color:var(--subtle);border-radius:3px 3px 0 0;height:32px;padding:4px 8px;
margin-bottom:-2px;box-shadow:inset 0 -2px 0 transparent}
.ed-tabs button:hover,.ed-tabs [role=tab]:hover{background:transparent;color:var(--fg);box-shadow:inset 0 -2px 0 var(--border-bold)}
.ed-tabs button.on,.ed-tabs [aria-selected=true]{color:var(--selected-fg);box-shadow:inset 0 -2px 0 var(--selected-fg)}
.ed{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px;align-items:start}
@media (max-width:1100px){.ed{grid-template-columns:minmax(0,1fr)}}
.ed-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}
.ed-panel{background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:4px 16px 16px;min-width:0}
.ed-panel h2{display:flex;align-items:center;gap:8px}.ed-panel h2 .sp{flex:1}
.ed-panel fieldset{border:0;border-top:1px solid var(--border);margin:16px 0 0;padding:8px 0 0;min-width:0}
.ed-panel legend{font-size:12px;line-height:16px;font-weight:600;color:var(--subtle);padding:0 8px 0 0}
.ed-panel .checks{display:flex;flex-wrap:wrap;gap:0 4px;margin-top:4px}
.ed-panel .checks label{font-weight:400;color:var(--fg);font-size:14px;line-height:20px;margin:4px 12px 4px 0;display:inline-flex;align-items:center}
.ed-panel p{margin:16px 0 0}
.ed-fields{width:100%;margin:8px 0}.ed-fields td,.ed-fields th{padding:4px}
.ed-fields tr:hover>td{background:transparent}
.ed-fields input,.ed-fields select{margin:0;font-size:14px}
.ed-bar{position:sticky;bottom:0;margin-top:16px;background:var(--overlay);border-radius:3px;padding:12px 16px;display:flex;
flex-wrap:wrap;gap:8px 12px;align-items:center;box-shadow:var(--shadow-overlay)}
.ed-bar .status{flex:1;min-width:min(220px,100%)}.ed-bar input{width:auto;flex:1;min-width:min(200px,100%);margin:0}
.ed-problems{margin:4px 0 0;padding-left:16px;color:var(--danger-fg)}
.bad-json{border-color:var(--danger)!important}

/* ---- the record type builder: the form, and what modules added beside it (read only) */
.fb-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,300px);gap:16px;align-items:start}
.fb-layout>.card,.fb-main>.card{margin:0}.fb-main{display:flex;flex-direction:column;gap:12px;min-width:0}
.fb-aside{position:sticky;top:72px}.fb-aside h2{margin:0 0 4px;font-size:16px}
.fb-module{border-top:1px solid var(--border);padding-top:8px;margin-top:8px}.fb-module h3{font-size:14px;margin:0 0 4px}
.fb-mod-fields{margin:0 0 8px;padding-left:16px}.fb-mod-fields li{margin:2px 0}
.fb-module pre.code{max-height:240px;overflow:auto;font-size:12px}
.fb-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.fb-head h2,form.fb h2.fb-h{margin:8px 0 0;font-size:16px;line-height:20px}
form.fb h3.fb-h3{margin:4px 0 0;font-size:14px}
.fb-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(180px,100%),1fr));gap:8px 12px;align-items:start}
.fb-grid>.field{margin:0}.fb-req{align-self:end;padding-bottom:6px}
.fb-fd{border:1px solid var(--border);border-radius:3px;padding:4px 12px;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;scroll-margin-top:72px}
.fb-fd>details{flex:1 1 280px;min-width:0}.fb-fd>details>summary{cursor:pointer;padding:6px 0;overflow-wrap:anywhere}
.fb-fd>details[open]{flex-basis:100%}.fb-fd[aria-invalid=true]{border-color:var(--danger)}
.fb-body{display:flex;flex-direction:column;gap:8px;padding:4px 0 8px}
.fb-fd>.fb-row-actions{margin-left:auto}
fieldset.fb-field,fieldset.fb-part,fieldset.fb-lc{border:1px solid var(--border);border-radius:3px;margin:0;padding:8px 12px 12px;
min-width:0;display:flex;flex-direction:column;gap:8px;scroll-margin-top:72px}
fieldset.fb-field>legend,fieldset.fb-part>legend{padding:0 4px;font-weight:600;font-size:14px;color:var(--fg)}
fieldset.fb-field[aria-invalid=true],fieldset.fb-lc[aria-invalid=true]{border-color:var(--danger)}
fieldset.fb-chips{margin:0}fieldset.fb-chips[aria-invalid=true]{outline:2px solid var(--danger);outline-offset:4px;border-radius:3px}
.fb-when[hidden]{display:none}.fb-when{display:flex;flex-direction:column;gap:8px}
.fb-tab{border-left:3px solid var(--border);padding-left:12px;display:flex;flex-direction:column;gap:8px}
.fb-row-actions{justify-content:flex-end}
.fb-errors{margin:0;padding:4px 8px 4px 24px;border-radius:3px;background:var(--danger-bg);color:var(--danger-fg);font-size:13px}
.fb-more,.fb-note{margin:0;font-size:12px;overflow-wrap:anywhere}
.fb-keep pre.code{font-size:12px;overflow:auto}
@media (max-width:900px){.fb-layout{grid-template-columns:minmax(0,1fr)}.fb-aside{position:static}}

/* ---- narrow screens: side panel first, single column, 16px gutter */
@media (max-width:900px){
  .shell.has-subnav{grid-template-columns:minmax(0,1fr)}
  .subnav{position:static;height:auto;max-height:40vh;border-right:0;border-bottom:1px solid var(--border)}
  .owner-rail{display:flex;gap:4px;overflow-x:auto;padding:8px 16px;max-height:none}
  .owner-rail .sec{display:none}.owner-rail ul{display:contents}
  .owner-rail a{white-space:nowrap;border:1px solid var(--border);border-radius:16px;padding:2px 10px}
  .shell{grid-template-columns:minmax(0,1fr)}
  .side{display:none;position:fixed;top:56px;left:0;width:min(280px,100vw);z-index:30;box-shadow:var(--shadow-overlay)}
  .side.open{display:block}
  .menu-toggle{display:grid;border:0;background:none;padding:0;place-items:center;width:32px;height:32px;border-radius:3px;cursor:pointer;color:var(--subtle)}
  .menu-toggle:hover{background:var(--neutral-hover)}
  .top form.search{display:none}
  .me .name{display:none}
  main{padding:16px 16px 32px}
  .issue{grid-template-columns:minmax(0,1fr)}
  .issue-side,.issue>.details{order:-1;position:static}
  .details{position:static}
}
/* the practice calendar: a column per member per day, rows of slots; a list on a phone */
.cal-bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between;margin:0 0 12px}
.cal-nav,.cal-views,.cal-who{display:flex;gap:4px;align-items:center;margin:0}
.cal-who select{width:auto;margin:0}.cal-who label{margin:0}
.cal-title{font-size:16px;line-height:20px;margin:0 0 0 8px}
.cal-views .btn.selected{background:var(--selected-bg);color:var(--selected-fg)}
.cal-wrap{overflow-x:auto;position:relative;border:1px solid var(--border);border-radius:8px;background:var(--surface)}
.cal{display:grid;grid-template-columns:52px repeat(var(--cols),minmax(132px,1fr));min-width:min-content}
.cal-corner{border-bottom:1px solid var(--border);background:var(--surface)}
.cal-day{text-align:center;font-weight:600;font-size:13px;padding:6px 4px;border-left:1px solid var(--border);
  border-bottom:1px solid var(--border);background:var(--sunken)}
.cal-day a{color:var(--fg);text-decoration:none}.cal-day.today a{color:var(--brand)}
.cal-day.today{box-shadow:inset 0 -2px 0 var(--brand)}
.cal-who-h{font-size:12px;color:var(--subtle);padding:4px 6px;text-align:center;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;border-left:1px solid var(--border);border-bottom:1px solid var(--border)}
.cal-times,.cal-col{display:grid;grid-template-rows:repeat(var(--rows),26px);grid-template-columns:minmax(0,1fr)}
.cal-time{grid-column:1;font-size:11px;color:var(--subtlest);text-align:right;padding:0 6px;transform:translateY(-6px)}
.cal-col{border-left:1px solid var(--border);position:relative}
.cal-col.today{background:color-mix(in srgb,var(--selected-bg) 35%,transparent)}
.cal-col>*{grid-column:1}
.cal-cell,.cal-slot,.cal-off{border-top:1px dotted var(--border)}
.cal-off{background:var(--sunken)}
.cal-slot{display:block;font-size:11px;padding:0 6px;color:transparent;text-decoration:none;line-height:25px}
.cal-slot:hover,.cal-slot:focus-visible{background:var(--selected-bg);color:var(--selected-fg)}
.cal-ev{z-index:1;margin:1px 3px;padding:2px 6px;border-radius:4px;font-size:12px;line-height:16px;overflow:hidden;
  background:var(--info-bg);color:var(--fg);border-left:3px solid var(--brand);text-decoration:none;min-height:0;
  white-space:nowrap;text-overflow:ellipsis}
a.cal-ev:hover{background:var(--selected-bg);text-decoration:none}
.cal-ev .t{color:var(--subtle);font-size:11px}.cal-ev .why{color:var(--subtle)}
.cal-ev .st{color:var(--subtlest);font-size:11px}
.cal-ev.busy{background:var(--neutral);border-left-color:var(--border-bold);color:var(--subtle)}
.cal-ev.cancelled{background:transparent;border:1px dashed var(--border-bold);border-left-width:3px;color:var(--subtlest)}
.cal-ev.cancelled b,.cal-ev.cancelled .why{text-decoration:line-through}
.cal-now{z-index:2;height:2px;align-self:start;margin-top:calc(var(--f) * 26px);background:var(--danger);
  pointer-events:none;position:relative}
.cal-now::before{content:"";position:absolute;left:-4px;top:-3px;width:8px;height:8px;border-radius:50%;
  background:var(--danger)}
.cal-list{display:none}
.cal-listday{margin:0 0 16px}.cal-listday h3{font-size:14px;margin:0 0 6px}
.cal-listday li{display:flex;flex-direction:column;gap:2px;padding:4px 0}
.cal-listday .cal-ev{margin:0;display:block;white-space:normal}.cal-listday .who{font-size:12px}
.cal-free{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.cal-legend{font-size:12px;margin-top:8px}
.cal-col>.cal-ev[style*="--lanes"]{width:calc(100% / var(--lanes) - 4px);margin-left:calc(var(--lane) * 100% / var(--lanes) + 2px);
  margin-right:0;box-sizing:border-box}
.cal-ev.marked-raised{border-left-color:var(--warning-icon)}
.cal-ev.marked-high{border-left-color:var(--danger);background:var(--danger-bg)}
.cal-ev.over{box-shadow:inset 0 0 0 1px var(--danger)}
.cal-pri{font-size:10px;line-height:14px;padding:0 4px;border-radius:3px;font-weight:700;text-transform:uppercase}
.cal-pri.high{background:var(--danger);color:var(--danger-on)}
.cal-pri.raised{background:var(--warning-bg);color:var(--warning-fg);box-shadow:inset 0 0 0 1px var(--warning-icon)}
.cal-key{display:inline-block;width:12px;height:10px;border-radius:2px;vertical-align:-1px}
.cal-key.over{box-shadow:inset 0 0 0 1px var(--danger);background:var(--danger-bg)}
.cal-squeeze{margin-left:auto}
fieldset.overbook{border:1px solid var(--border);border-radius:4px;padding:8px 10px;margin:0;display:grid;gap:6px}
.booking-when{font-size:14px;margin:0}
@media (max-width:600px){.cal-wrap{display:none}.cal-list{display:block}.cal-title{margin:0;flex-basis:100%}}
@media (max-width:480px){
  main table:not(.kv){display:block;overflow-x:auto;white-space:nowrap;position:relative}
  .top{gap:4px;padding:0 8px}
  .logo span:not(.mark){display:none}
  .board{grid-auto-columns:minmax(240px,85vw)}
}
""")

ICONS = {
    "inbox": '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
    "board": '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="11" rx="1"/>'
             '<rect x="17" y="4" width="4" height="7" rx="1"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/>'
            '<circle cx="4" cy="18" r="1"/>',
    "people": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/>'
              '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.8.8 3 2.6 3.5 5.2"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "flow": '<rect x="2" y="9" width="6" height="6" rx="1"/><rect x="16" y="3" width="6" height="6" rx="1"/>'
            '<rect x="16" y="15" width="6" height="6" rx="1"/><path d="M8 12h4M12 6v12M12 6h4M12 18h4"/>',
    "team": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4.4-6 8-6s7 2 8 6"/>',
    "track": '<path d="M4 6h10M4 12h16M4 18h12"/><path d="M18 3l3 3-3 3"/>',
    "outbox": '<path d="M4 4h16v12H4z"/><path d="M4 4l8 7 8-7"/><path d="M8 20h8"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "records": '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>',
    "module": '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/>'
              '<rect x="3" y="13" width="8" height="8" rx="1"/><path d="M17 13v8M13 17h8"/>',
    "block": '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
    "skill": '<path d="M12 3l2.3 5.4 5.7.5-4.3 3.8 1.3 5.6L12 15.4 7 18.3l1.3-5.6L4 8.9l5.7-.5z"/>',
    "access": '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1'
                'M4.9 19.1L7 17M17 7l2.1-2.1"/>',
}

JS = r"""
(function () {
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "value") n.value = v;
    else if (k === "checked") n.checked = !!v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(3)) if (c !== null && c !== undefined && c !== false)
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
function sv(tag, attrs, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
const clip = (t, n) => (t.length > n ? t.slice(0, n - 1) + "…" : t);

// ------------------------------------------------------------------ workflow diagram
function columns(steps) {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const depth = new Map(), busy = new Set();
  const d = (s) => {
    if (depth.has(s.id)) return depth.get(s.id);
    if (busy.has(s.id)) return 0;  // a loop: the server reports it; just draw something
    busy.add(s.id);
    let v = 0;
    for (const a of s.after || []) { const p = byId.get(a); if (p) v = Math.max(v, d(p) + 1); }
    busy.delete(s.id); depth.set(s.id, v);
    return v;
  };
  steps.forEach(d);
  const cols = [];
  for (const s of steps) (cols[depth.get(s.id)] = cols[depth.get(s.id)] || []).push(s);
  return cols.filter(Boolean);
}
let flowSeq = 0;
function drawFlow(svg, data, opts) {
  opts = opts || {};
  const steps = data.steps || [], states = data.states || {}, links = data.links || {};
  svg.textContent = "";
  const W = 190, H = 60, GX = 54, GY = 14, P = 14;
  const cols = columns(steps), pos = new Map();
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const height = P * 2 + rows * H + (rows - 1) * GY;
  cols.forEach((col, i) => {
    const ch = col.length * H + (col.length - 1) * GY, y0 = P + (height - 2 * P - ch) / 2;
    col.forEach((s, j) => pos.set(s.id, { x: P + i * (W + GX), y: y0 + j * (H + GY) }));
  });
  const width = Math.max(240, P * 2 + cols.length * W + Math.max(0, cols.length - 1) * GX);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width); svg.setAttribute("height", height);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Workflow diagram: " + steps.map((s) => s.title).join(", "));
  const mid = "arr" + (++flowSeq);
  const defs = sv("defs"), mk = sv("marker", { id: mid, viewBox: "0 0 10 10", refX: "9", refY: "5",
    markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
  mk.append(sv("path", { d: "M0 0L10 5L0 10z", class: "fl-arrowhead" })); defs.append(mk); svg.append(defs);
  for (const s of steps) for (const a of s.after || []) {
    const p = pos.get(a), q = pos.get(s.id);
    if (!p || !q) continue;
    const x1 = p.x + W, y1 = p.y + H / 2, x2 = q.x - 2, y2 = q.y + H / 2, c = Math.max(24, (x2 - x1) / 2);
    const path = sv("path", { d: `M${x1} ${y1}C${x1 + c} ${y1} ${x2 - c} ${y2} ${x2} ${y2}`,
      class: "fl-edge" + (s.when ? " cond" : ""), "marker-end": `url(#${mid})` });
    if (s.when) path.append(sv("title", {}, "only " + s.when));
    svg.append(path);
  }
  for (const s of steps) {
    const { x, y } = pos.get(s.id), st = states[s.id];
    const clickable = !!(opts.onSelect || links[s.id]);
    const g = sv("g", { class: `fl-node k-${s.kind} ${st ? "st-" + st : ""} ${opts.selected === s.id ? "sel" : ""} ${clickable ? "click" : ""}`,
      tabindex: clickable ? "0" : "-1" });
    g.append(sv("title", {}, `${s.title} (${s.kind}${s.role ? ", " + s.role : ""})${s.when ? " — only " + s.when : ""}${s.timing ? " — " + s.timing : ""}`));
    g.append(sv("rect", { x, y, width: W, height: H, rx: 7, class: "box" }));
    g.append(sv("rect", { x, y: y + 6, width: 4, height: H - 12, rx: 2, class: "bar" }));
    const kind = (s.kind === "gate" ? "decision" : s.kind).toUpperCase();
    g.append(sv("text", { x: x + 14, y: y + 18, class: "k" }, clip(kind + " · " + (s.role || s.action || ""), 30)));
    g.append(sv("text", { x: x + 14, y: y + 36 }, clip(s.title || s.id, 26)));
    g.append(sv("text", { x: x + 14, y: y + 52, class: "s" }, st ? st : (s.when ? clip(s.when, 28) : s.timing ? clip(s.timing, 28) : s.id)));
    if (s.agents_may) {
      g.append(sv("rect", { x: x + W - 30, y: y + 7, width: 22, height: 14, rx: 3, class: "ai" }));
      g.append(sv("text", { x: x + W - 25, y: y + 18, class: "ai-t" }, "AI"));
    }
    if (clickable) {
      const go = () => (opts.onSelect ? opts.onSelect(s.id) : (location.href = links[s.id]));
      g.addEventListener("click", (ev) => { ev.stopPropagation(); go(); });
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    }
    svg.append(g);
  }
}
window.drawFlow = drawFlow;

// ------------------------------------------------------------------ confirmation dialog (instead of confirm())
let dlg = null;
function confirmDialog() {
  if (dlg) return dlg;
  const title = el("h2", { id: "ph-confirm-title" }), body = el("p", { id: "ph-confirm-msg", class: "modal-body" });
  const cancel = el("button", { type: "submit", value: "cancel", class: "subtle", autofocus: true }, "Cancel");
  const ok = el("button", { type: "submit", value: "ok", class: "primary" }, "Confirm");
  const d = el("dialog", { class: "modal", "aria-labelledby": "ph-confirm-title", "aria-describedby": "ph-confirm-msg" },
    el("form", { method: "dialog" }, title, body, el("div", { class: "modal-actions" }, cancel, ok)));
  d.addEventListener("click", (ev) => { if (ev.target === d) d.close("cancel"); });  // a click on the backdrop cancels
  document.body.append(d);
  dlg = { d, title, body, cancel, ok };
  return dlg;
}
// Resolves true when the person confirms; Escape, Cancel or the backdrop resolve false. Focus goes back to the trigger.
function askConfirm(trigger, message, o) {
  o = o || {};
  const { d, title, body, cancel, ok } = confirmDialog();
  title.textContent = o.title || "Are you sure?";
  body.textContent = message;
  ok.textContent = o.label || "Confirm";
  ok.className = o.danger ? "danger" : "primary";
  d.returnValue = "";
  return new Promise((resolve) => {
    d.addEventListener("close", () => {
      if (trigger && trigger.isConnected) trigger.focus();
      resolve(d.returnValue === "ok");
    }, { once: true });
    d.showModal();
    cancel.focus();
  });
}
window.askConfirm = askConfirm;
function confirmOptions(...nodes) {
  const has = (a) => nodes.some((n) => n && n.hasAttribute(a));
  const get = (a) => { for (const n of nodes) if (n && n.getAttribute(a)) return n.getAttribute(a); return null; };
  return { danger: has("data-confirm-danger"), title: get("data-confirm-title"), label: get("data-confirm-label") };
}
const confirmed = new WeakSet();  // triggers that were just confirmed and are being replayed
// <textarea data-check="/api/..." data-status="id">: checked against the hub as you type.
document.querySelectorAll("textarea[data-check]").forEach((t) => {
  const s = document.getElementById(t.dataset.status);
  let timer;
  async function check() {
    try {
      const r = await fetch(t.dataset.check, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source: t.value }) });
      if (r.status === 401) { s.textContent = "Sign in again to check this."; return; }
      const j = await r.json();
      s.textContent = "";
      if (!r.ok && !(j.problems && j.problems.length)) { s.textContent = "The hub could not check this: " + (j.error || r.status); return; }
      if (!j.problems || !j.problems.length) {
        const ok = document.createElement("span"); ok.className = "loz done"; ok.textContent = "ready";
        s.append(ok, " The hub accepts this."); return;
      }
      const b = document.createElement("span"); b.className = "loz bad";
      b.textContent = j.problems.length + " problem" + (j.problems.length === 1 ? "" : "s");
      const ul = document.createElement("ul"); ul.className = "ed-problems";
      for (const p of j.problems) { const li = document.createElement("li"); li.textContent = p; ul.append(li); }
      s.append(b, ul);
    } catch (e) { s.textContent = "Could not reach the hub to check."; }
  }
  t.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 500); });
  if (t.value.trim() && !s.dataset.checked) check();
});
// The record type builder: a field's choices and its target show for the types that use them.
document.querySelectorAll("select[data-fb-type]").forEach((sel) => {
  const row = sel.closest(".fb-field");
  sel.addEventListener("change", () => row.querySelectorAll("[data-fb-for]").forEach((box) => {
    if (box.dataset.fbFor === sel.value) box.hidden = false;
    else if (!box.querySelector("input:checked, option:checked")) box.hidden = true;
  }));
});
// The settings ribbon, a strip on a narrow screen: the lit item scrolled into view (the page itself stays put).
(function () {
  const r = document.querySelector(".ribbon"), on = r && r.querySelector("a.on");
  if (!r || !on || r.scrollWidth <= r.clientWidth) return;
  r.scrollLeft = on.offsetLeft - (r.clientWidth - on.offsetWidth) / 2;
})();
// The narrow-screen menu: a real button, so it is reachable and announced.
document.addEventListener("click", (ev) => {
  const b = ev.target.closest(".menu-toggle");
  if (!b) return;
  const side = document.getElementById(b.getAttribute("aria-controls"));
  const open = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(open));
  if (side) side.classList.toggle("open", open);
});
// A button with data-confirm, inside a form or not.
document.addEventListener("click", (ev) => {
  const b = ev.target.closest && ev.target.closest("button[data-confirm]");
  if (!b || b.disabled) return;
  if (confirmed.has(b)) { confirmed.delete(b); return; }
  const f = b.form, submits = f && (b.type || "submit") === "submit";
  if (submits && !b.formNoValidate && !f.noValidate && !f.checkValidity()) return;  // let the browser show what is missing
  ev.preventDefault(); ev.stopImmediatePropagation();
  const o = confirmOptions(b, f);
  if (!o.label) o.label = b.textContent.trim() || null;
  askConfirm(b, b.getAttribute("data-confirm"), o).then((yes) => {
    if (!yes) return;
    if (submits) { confirmed.add(f); f.requestSubmit(b); confirmed.delete(f); } else { confirmed.add(b); b.click(); confirmed.delete(b); }
  });
}, true);
// A form with data-confirm, whichever way it is submitted.
document.addEventListener("submit", (ev) => {
  const f = ev.target;
  if (!(f instanceof HTMLFormElement)) return;
  if (confirmed.has(f)) { confirmed.delete(f); return; }
  if (!f.hasAttribute("data-confirm")) return;
  const sub = ev.submitter || null;
  ev.preventDefault(); ev.stopPropagation();  // the form's own submit handlers run on the confirmed replay
  const o = confirmOptions(sub, f);
  if (!o.label && sub) o.label = sub.textContent.trim() || null;
  askConfirm(sub || f.querySelector("button, [type=submit]"), f.getAttribute("data-confirm"), o).then((yes) => {
    if (!yes) return;
    confirmed.add(f);
    if (sub && sub.form === f) f.requestSubmit(sub); else f.requestSubmit();
    confirmed.delete(f);  // requestSubmit dispatches synchronously; never leave a stale pass behind
  });
}, true);
// A form posts once: a second press (or Enter) while it is being sent does nothing, and its buttons say so, so
// pressing Start twice never starts two of anything. A submit another check stopped (nothing was sent) frees it again;
// a page brought back with the browser's Back, or a post that only downloaded a file, frees it too.
function freeForm(f) {
  delete f.dataset.sending; delete f.dataset.posted; f.removeAttribute("aria-busy");
  f.querySelectorAll("button[data-was-enabled]").forEach((b) => { b.disabled = false; b.removeAttribute("data-was-enabled"); });
}
document.addEventListener("submit", (ev) => {
  const f = ev.target;
  if (!(f instanceof HTMLFormElement) || ev.defaultPrevented || (f.getAttribute("method") || "").toLowerCase() !== "post") return;
  if (f.dataset.sending) { ev.preventDefault(); ev.stopImmediatePropagation(); return; }
  f.dataset.sending = "1";
  setTimeout(() => {  // after the post has taken the form's values (a disabled button sends none)
    if (ev.defaultPrevented && !f.dataset.posted) { freeForm(f); return; }
    f.setAttribute("aria-busy", "true");
    f.querySelectorAll("button:not([type=button]):not([disabled])").forEach((b) => { b.disabled = true; b.setAttribute("data-was-enabled", ""); });
    setTimeout(() => { if (f.isConnected) freeForm(f); }, 15000);
  }, 0);
}, true);
window.addEventListener("pageshow", (ev) => { if (ev.persisted) document.querySelectorAll("form[data-sending]").forEach(freeForm); });

// ------------------------------------------------------------------ avatar menu
function avatarMenu(m) {
  const btn = m.querySelector("button[aria-haspopup]"), menu = m.querySelector("[role=menu]");
  if (!btn || !menu) return;
  const items = () => [...menu.querySelectorAll("[role=menuitem]")];
  items().forEach((x) => (x.tabIndex = -1));
  const isOpen = () => !menu.hidden;
  function open(focus) {
    menu.hidden = false; btn.setAttribute("aria-expanded", "true");
    const it = items();
    if (focus === "first" && it.length) it[0].focus();
    if (focus === "last" && it.length) it[it.length - 1].focus();
  }
  function close(refocus) {
    if (!isOpen()) return;
    menu.hidden = true; btn.setAttribute("aria-expanded", "false");
    if (refocus) btn.focus();
  }
  btn.addEventListener("click", () => (isOpen() ? close(false) : open(btn.matches(":focus-visible") ? "first" : null)));
  btn.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); open("first"); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); open("last"); }
    else if (ev.key === "Escape") close(true);
  });
  menu.addEventListener("keydown", (ev) => {
    const it = items(), i = it.indexOf(document.activeElement);
    const n = { ArrowDown: (i + 1) % it.length, ArrowUp: (i - 1 + it.length) % it.length, Home: 0, End: it.length - 1 }[ev.key];
    if (n !== undefined && it.length) { ev.preventDefault(); it[n].focus(); }
    else if (ev.key === "Escape") { ev.preventDefault(); close(true); }
    else if (ev.key === "Tab") close(false);
  });
  document.addEventListener("click", (ev) => { if (!m.contains(ev.target)) close(false); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && isOpen()) close(true); });
}

// ------------------------------------------------------------------ formatted-text editor
// A Markdown field edited as formatted text. The text box stays the form's value: the editor writes Markdown back
// into it. Everything shown in the editor is built here from Markdown, escaped as the server escapes it, so no HTML
// from a paste or a drop reaches the page as it was.
const MD = (() => {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  const SAFE_URL = /^(https?:\/\/|mailto:|\/(?![\/\\]))[^\s"'<>\\]*$/i;
  const MENTION = /^\[\[([^\[\]\n|]{1,200})(?:\|([^\[\]\n]{1,200}))?\]\]$/;
  const INLINE = /(`[^`\n]+`)|(\[\[[^\[\]\n]{1,400}\]\])|(\[([^\[\]\n]{1,300})\]\(([^()\s]{1,2000})\))|(\*\*([^*\n]+?)\*\*)|(~~([^~\n]+?)~~)|((?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*]))/g;
  const SEP = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;
  const BLOCK = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "BLOCKQUOTE", "PRE", "HR", "TABLE", "ASIDE"]);

  function mention(source, titles) {
    const bar = source.indexOf("|");
    const target = bar < 0 ? source : source.slice(0, bar), words = bar < 0 ? "" : source.slice(bar + 1);
    return `<span class="mention" contenteditable="false" data-mention="${esc(source)}">${esc(words || titles[target] || target)}</span>`;
  }
  function inline(s, titles, inLink) {
    let out = "", pos = 0, m;
    const re = new RegExp(INLINE.source, "g");
    while ((m = re.exec(s))) {
      out += esc(s.slice(pos, m.index)); pos = re.lastIndex;
      if (m[1]) out += "<code>" + esc(m[1].slice(1, -1)) + "</code>";
      else if (m[2]) {
        const mm = MENTION.exec(m[2]);
        if (!mm) { out += esc(m[2]); continue; }
        const src = mm[1].trim() + (mm[2] && mm[2].trim() ? "|" + mm[2].trim() : "");
        out += inLink ? esc(mm[2] || mm[1]) : mention(src, titles);
      } else if (m[3]) out += SAFE_URL.test(m[5]) && !inLink
        ? `<a href="${esc(m[5])}">${inline(m[4], titles, true)}</a>` : inline(m[4], titles, inLink);
      else if (m[6]) out += "<strong>" + inline(m[7], titles, inLink) + "</strong>";
      else if (m[8]) out += "<del>" + inline(m[9], titles, inLink) + "</del>";
      else out += "<em>" + inline(m[11], titles, inLink) + "</em>";
    }
    return out + esc(s.slice(pos));
  }
  const CALLOUTS = { note: "Note", tip: "Tip", warning: "Warning", important: "Important" };
  function item(x, titles) {  // a checklist item when it starts [ ] or [x], as markup.py has it
    const t = /^\[( |x|X)\]\s+([\s\S]*)$/.exec(x);
    if (!t) return `<li>${inline(x, titles)}</li>`;
    const done = t[1] !== " ";
    return `<li class="task${done ? " done" : ""}"><span class="box${done ? " done" : ""}" aria-hidden="true">${done ? "✓" : ""}</span>`
      + `<span class="visually-hidden">${done ? "done: " : "to do: "}</span>${inline(t[2], titles)}</li>`;
  }
  const cells = (line) => line.trim().replace(/^\|+|\|+$/g, "").split("|").map((c) => c.trim());

  // Markdown to HTML: the same subset, read the same way, as practicehub/markup.py. ``blocks``: the same, one
  // top-level element at a time, each with the place in the text it was read from (from, to: UTF-16 offsets), so
  // that an editor can tell which part of the markdown each element on the screen stands for.
  function blocks(text, titles, depth) {
    titles = titles || {}; depth = depth || 0;
    const src = String(text || ""), lines = src.split("\n"), starts = [], out = [];
    for (let k = 0, at = 0; k < lines.length; k++) { starts.push(at); at += lines[k].length + 1; }
    const span = (a, b) => [starts[a], starts[b - 1] + lines[b - 1].length];
    const push = (html, a, b) => { const [from, to] = span(a, Math.min(b, lines.length)); out.push({ html, from, to }); };
    let para = [], pa = 0, pb = 0, i = 0, m;
    const flush = () => { if (para.length) { push("<p>" + para.map((p) => inline(p, titles)).join("<br>") + "</p>", pa, pb); para = []; } };
    while (i < lines.length) {
      const s = lines[i].trim(), a = i;
      if (s.startsWith("```")) {
        flush(); const code = []; i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++].replace(/\r$/, ""));
        i++; push("<pre><code>" + esc(code.join("\n")) + "</code></pre>", a, i); continue;
      }
      if (!s) { flush(); i++; continue; }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(s)) && m[2].replace(/[\s#]+$/, "")) {
        flush(); const n = Math.max(2, m[1].length); i++;
        push(`<h${n}>${inline(m[2].replace(/\s+$/, "").replace(/#+$/, "").replace(/\s+$/, ""), titles)}</h${n}>`, a, i); continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(s)) { flush(); i++; push("<hr>", a, i); continue; }
      if (s.startsWith(">")) {
        flush(); const q = [];
        while (i < lines.length && lines[i].trim().startsWith(">")) q.push(lines[i++].trim().slice(1).replace(/^\s+/, ""));
        const call = q.length ? /^\[!(note|tip|warning|important)\]$/i.exec(q[0].trim()) : null;
        if (call) q.shift();
        const inner = depth < 4 ? toHtml(q.join("\n"), titles, depth + 1)
          : "<p>" + q.filter(Boolean).map((x) => inline(x, titles)).join("<br>") + "</p>";
        if (call) { const k = call[1].toLowerCase();
          push(`<aside class="callout ${k}" role="note"><p class="callout-title">${CALLOUTS[k]}</p>${inner}</aside>`, a, i); }
        else push("<blockquote>" + inner + "</blockquote>", a, i);
        continue;
      }
      if (s.startsWith("|") && i + 1 < lines.length && SEP.test(lines[i + 1].trim())) {
        flush(); const head = cells(s); i += 2; const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
        push("<table><thead><tr>" + head.map((c) => `<th>${inline(c, titles)}</th>`).join("") + "</tr></thead><tbody>"
          + rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c, titles)}</td>`).join("") + "</tr>").join("") + "</tbody></table>", a, i);
        continue;
      }
      if ((m = /^([-*+]|\d{1,3}\.)\s+/.exec(s))) {
        flush(); const ordered = /\d/.test(m[1][0]), items = [];
        while (i < lines.length) {
          const m2 = /^\s*([-*+]|\d{1,3}\.)\s+(.*)$/.exec(lines[i].replace(/\r$/, ""));
          if (m2 && /\d/.test(m2[1][0]) === ordered) items.push(m2[2]);
          else if (/^( {2}|\t)/.test(lines[i]) && lines[i].trim() && items.length) items[items.length - 1] += " " + lines[i].trim();
          else break;
          i++;
        }
        const tag = ordered ? "ol" : "ul";
        push(`<${tag}>` + items.map((x) => item(x, titles)).join("") + `</${tag}>`, a, i);
        continue;
      }
      if (!para.length) pa = i;
      para.push(s); i++; pb = i;
    }
    flush();
    return out;
  }
  function toHtml(text, titles, depth) {
    return blocks(text, titles, depth).map((b) => b.html).join("");
  }

  // HTML (the editor's, or a paste's) to Markdown. Whatever it doesn't know keeps only its words.
  function inlineMd(node) {
    let s = "";
    for (const c of node.childNodes) {
      if (c.nodeType === 3) { s += c.nodeValue.replace(/ /g, " ").replace(/\n/g, " "); continue; }
      if (c.nodeType !== 1) continue;
      const t = c.tagName;
      if (c.dataset && c.dataset.mention) { s += "[[" + c.dataset.mention.replace(/[\[\]\n]/g, "") + "]]"; continue; }
      if (t === "BR") { s += "\n"; continue; }
      if (t === "SCRIPT" || t === "STYLE" || t === "TEMPLATE") continue;
      if (t === "CODE") { const x = c.textContent.replace(/[`\n]/g, ""); if (x) s += "`" + x + "`"; continue; }
      const inner = inlineMd(c);
      if (!inner.trim()) { s += inner; continue; }
      const lead = inner.match(/^\s*/)[0], trail = inner.match(/\s*$/)[0], core = inner.trim();
      const wrap = (mark) => lead + mark + core + mark + trail;
      const w = c.style ? c.style.fontWeight : "";
      if (t === "STRONG" || t === "B" || w === "bold" || +w >= 600) s += wrap("**");
      else if (t === "EM" || t === "I" || (c.style && c.style.fontStyle === "italic")) s += wrap("*");
      else if (t === "DEL" || t === "S" || t === "STRIKE") s += wrap("~~");
      else if (t === "A" && SAFE_URL.test(c.getAttribute("href") || "")) s += lead + "[" + core.replace(/[\[\]\n]/g, "") + "](" + c.getAttribute("href") + ")" + trail;
      else s += inner;
    }
    return s;
  }
  const hasBlock = (n) => [...n.children].some((x) => BLOCK.has(x.tagName));
  function listMd(list, ordered) {
    const lines = []; let n = 1;
    const walk = (l) => {
      for (const li of l.children) {
        if (li.tagName !== "LI") continue;
        const own = li.cloneNode(true);
        own.querySelectorAll("ul,ol,.box,.visually-hidden").forEach((x) => x.remove());
        const x = inlineMd(own).replace(/\s*\n\s*/g, " ").trim();
        const box = li.classList.contains("task") ? (li.classList.contains("done") ? "[x] " : "[ ] ") : "";
        if (x) lines.push((ordered ? (n++) + ". " : "- ") + box + x);
        for (const sub of li.children) if (sub.tagName === "UL" || sub.tagName === "OL") walk(sub);
      }
    };
    walk(list);
    return lines.join("\n");
  }
  function tableMd(tb) {
    const rows = [...tb.querySelectorAll("tr")].map((tr) => [...tr.children].map((td) =>
      inlineMd(td).replace(/\s*\n\s*/g, " ").replace(/\|/g, "/").trim()));
    if (!rows.length) return "";
    const w = Math.max(...rows.map((r) => r.length));
    const line = (r) => "| " + [...r, ...Array(w - r.length).fill("")].join(" | ") + " |";
    return [line(rows[0]), "|" + " --- |".repeat(w), ...rows.slice(1).map(line)].join("\n");
  }
  function toMd(root) {
    const out = []; let loose = [];
    const flushLoose = () => {
      if (!loose.length) return;
      const p = document.createElement("p");
      loose.forEach((n) => p.append(n.cloneNode(true)));
      const x = inlineMd(p).replace(/^\n+|\n+$/g, "").trim();
      if (x) out.push(x);
      loose = [];
    };
    for (const c of root.childNodes) {
      const t = c.nodeType === 1 ? c.tagName : "";
      if (!BLOCK.has(t)) { if (!["SCRIPT", "STYLE", "TEMPLATE"].includes(t)) loose.push(c); continue; }
      flushLoose();
      if (/^H\d$/.test(t)) {
        const x = inlineMd(c).replace(/\s+/g, " ").trim();
        if (x) out.push("#".repeat(Math.min(4, Math.max(2, +t[1]))) + " " + x);
      } else if (t === "P" || t === "DIV") {
        const x = hasBlock(c) ? toMd(c) : inlineMd(c).replace(/^\n+|\n+$/g, "").trim();
        if (x) out.push(x);
      } else if (t === "UL" || t === "OL") { const x = listMd(c, t === "OL"); if (x) out.push(x); }
      else if (t === "ASIDE" && c.classList.contains("callout")) {
        const own = c.cloneNode(true), k = [...c.classList].find((x) => CALLOUTS[x]) || "note";
        own.querySelectorAll(".callout-title").forEach((x) => x.remove());
        const x = hasBlock(own) ? toMd(own) : inlineMd(own).trim();
        out.push(["[!" + k + "]", ...x.split("\n")].map((l) => (l ? "> " + l : ">")).join("\n"));
      } else if (t === "BLOCKQUOTE") {
        const x = hasBlock(c) ? toMd(c) : inlineMd(c).trim();
        if (x) out.push(x.split("\n").map((l) => (l ? "> " + l : ">")).join("\n"));
      } else if (t === "PRE") {  // an editor's line breaks in it may be <br> or a <div> a line
        const own = c.cloneNode(true);
        own.querySelectorAll("br").forEach((b) => b.replaceWith("\n"));
        own.querySelectorAll("div,p").forEach((d) => { if (d.previousSibling) d.before("\n"); });
        const x = own.textContent.replace(/\u200b/g, "").replace(/\n$/, "");
        out.push("```\n" + x.replace(/```/g, "ˋˋˋ") + "\n```");
      }
      else if (t === "HR") out.push("---");
      else if (t === "TABLE") { const x = tableMd(c); if (x) out.push(x); }
    }
    flushLoose();
    return out.join("\n\n");
  }
  return { toHtml, toMd, esc, blocks, inline };
})();

function mdEditor(area) {
  const label = area.closest("label");
  const start = area.parentElement.querySelector(`template.md-start[data-for="${area.id}"]`);
  const help = document.getElementById(area.id + "-help");
  const titlesList = document.getElementById(area.id + "-titles");
  const titles = {};
  const surface = el("div", { class: "md-surface prose", contenteditable: "true", role: "textbox", "aria-multiline": "true",
    spellcheck: "true" });
  // the start is the server's own rendering of the text: from it, the words each [[title]] shows
  if (start) start.content.querySelectorAll("[data-mention]").forEach((m) => {
    const src = m.dataset.mention, target = src.split("|")[0];
    if (!src.includes("|")) titles[target] = m.textContent.replace(/\s*\(no page or record found\)\s*$/, "");
  });
  surface.innerHTML = MD.toHtml(area.value, titles);
  const labelText = label ? label.firstChild : null;
  const lid = area.id + "-label";
  let host = area.parentElement;
  if (label) {
    const span = el("span", { id: lid, class: "md-label" }, (labelText && labelText.nodeType === 3 ? labelText.nodeValue : area.name));
    surface.setAttribute("aria-labelledby", lid);
    if (help) surface.setAttribute("aria-describedby", help.id);
    // the editor sits beside its label, not inside it: a click in it must not go to the hidden text box
    const wrap = el("div", { class: "md-field" }, span);
    label.replaceWith(wrap);
    label.querySelectorAll("textarea, template, datalist, small").forEach((x) => wrap.append(x));
    host = wrap;
  }
  const required = area.required;
  area.required = false;
  area.hidden = true;
  let mode = "visual";
  const sync = () => { if (mode === "visual") area.value = MD.toMd(surface); };
  let timer = 0;
  const later = () => { clearTimeout(timer); timer = setTimeout(sync, 250); };

  const cmd = (name, arg) => { surface.focus(); document.execCommand(name, false, arg); sync(); refresh(); };
  const inBlock = (tag) => {
    const sel = getSelection();
    let n = sel.rangeCount ? sel.anchorNode : null;
    while (n && n !== surface) { if (n.nodeType === 1 && n.tagName === tag) return true; n = n.parentNode; }
    return false;
  };
  const wrapCode = () => {
    const sel = getSelection();
    if (!sel.rangeCount || !surface.contains(sel.anchorNode)) return;
    const text = sel.toString().replace(/[`\n]/g, "");
    cmd("insertHTML", "<code>" + MD.esc(text || "code") + "</code>&#8203;");
  };
  const table = () => cmd("insertHTML", "<table><thead><tr><th>Heading</th><th>Heading</th></tr></thead><tbody>"
    + "<tr><td>&#8203;</td><td>&#8203;</td></tr><tr><td>&#8203;</td><td>&#8203;</td></tr></tbody></table><p><br></p>");

  // asking for an address or a title: a small form under the toolbar, never a browser dialog
  let saved = null;
  const askInput = el("input", { type: "text", class: "md-ask-in", "aria-label": "Address" });
  const askLabel = el("span", { class: "md-ask-label" });
  const askGo = el("button", { type: "button", class: "btn primary" }, "Add");
  const askCancel = el("button", { type: "button", class: "btn subtle" }, "Cancel");
  const ask = el("div", { class: "md-ask", hidden: true }, askLabel, askInput, askGo, askCancel);
  let askKind = "";
  const openAsk = (kind) => {
    const sel = getSelection();
    saved = sel.rangeCount && surface.contains(sel.anchorNode) ? sel.getRangeAt(0).cloneRange() : null;
    askKind = kind;
    askLabel.textContent = kind === "link" ? "Link to (https://…, mailto: or a page in this hub)" : "Page title, or Type/id";
    askInput.setAttribute("aria-label", askLabel.textContent);
    if (kind === "mention" && titlesList) askInput.setAttribute("list", titlesList.id); else askInput.removeAttribute("list");
    askInput.value = kind === "link" ? "https://" : "";
    ask.hidden = false; askInput.focus(); askInput.select();
  };
  const closeAsk = () => { ask.hidden = true; askHint.textContent = ""; surface.focus(); if (saved) { const s = getSelection(); s.removeAllRanges(); s.addRange(saved); } };
  const askHint = el("span", { class: "md-ask-hint", role: "status" });
  ask.append(askHint);
  const finishAsk = () => {
    const v = askInput.value.trim();
    if (!v) return closeAsk();
    if (askKind === "link") {
      if (!/^(https?:\/\/[^\s"'<>\\]+|mailto:[^\s"'<>\\]+|\/(?![\/\\])[^\s"'<>\\]*)$/i.test(v)) {
        askHint.textContent = "Use an address starting https://, mailto: or / (a page in this hub)."; return;
      }
      closeAsk();
      const words = saved && !saved.collapsed ? "" : MD.esc(v);
      if (words) cmd("insertHTML", `<a href="${MD.esc(v)}">${words}</a>&#8203;`); else cmd("createLink", v);
    } else {
      const src = v.replace(/[\[\]\n|]/g, "").slice(0, 200);
      closeAsk();
      cmd("insertHTML", `<span class="mention" contenteditable="false" data-mention="${MD.esc(src)}">${MD.esc(titles[src] || src)}</span>&nbsp;`);
    }
  };
  askGo.addEventListener("click", finishAsk);
  askCancel.addEventListener("click", closeAsk);
  askInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finishAsk(); }
    if (ev.key === "Escape") { ev.preventDefault(); closeAsk(); }
  });

  const style = el("select", { class: "md-style", "aria-label": "Text style", onchange: () => {
    const v = style.value;
    cmd("formatBlock", v === "p" ? "<p>" : `<${v}>`);
  } }, el("option", { value: "p" }, "Normal text"), el("option", { value: "h2" }, "Heading"),
     el("option", { value: "h3" }, "Subheading"), el("option", { value: "h4" }, "Small heading"),
     el("option", { value: "blockquote" }, "Quote"), el("option", { value: "pre" }, "Code block"));
  const B = (label, key, text, fn) => el("button", { type: "button", class: "md-btn", title: key ? `${label} (${key})` : label,
    "aria-label": label, "data-act": text, onmousedown: (ev) => ev.preventDefault(), onclick: fn }, text);
  const mark = { b: "bold", i: "italic", s: "strikeThrough" };
  const btns = [
    B("Bold", "Ctrl+B", "B", () => cmd("bold")), B("Italic", "Ctrl+I", "I", () => cmd("italic")),
    B("Strikethrough", "", "S", () => cmd("strikeThrough")), B("Code", "", "</>", wrapCode),
    B("Bulleted list", "", "•", () => cmd("insertUnorderedList")), B("Numbered list", "", "1.", () => cmd("insertOrderedList")),
    B("Link", "Ctrl+K", "Link", () => openAsk("link")), B("Link a page or record", "[[", "[[ ]]", () => openAsk("mention")),
    B("Table", "", "Table", table), B("Divider", "", "—", () => cmd("insertHorizontalRule")),
  ];
  const toggles = btns.slice(0, 3);
  const modeVis = el("button", { type: "button", class: "md-mode", "aria-pressed": "true" }, "Visual");
  const modeSrc = el("button", { type: "button", class: "md-mode", "aria-pressed": "false" }, "Markdown");
  const bar = el("div", { class: "md-bar", role: "toolbar", "aria-label": "Formatting", "aria-controls": area.id },
    style, el("span", { class: "md-sep" }), btns.slice(0, 4), el("span", { class: "md-sep" }), btns.slice(4, 6),
    el("span", { class: "md-sep" }), btns.slice(6), el("span", { class: "md-grow" }), modeVis, modeSrc);
  const box = el("div", { class: "md-editor" }, bar, ask, surface);
  host.insertBefore(box, area);
  area.classList.add("md-source");

  const refresh = () => {
    if (mode !== "visual") return;
    for (const [k, b] of [["b", toggles[0]], ["i", toggles[1]], ["s", toggles[2]]]) {
      let on = false;
      try { on = document.queryCommandState(mark[k]); } catch (err) { on = false; }
      b.setAttribute("aria-pressed", String(on));
    }
    const sel = getSelection();
    let n = sel.rangeCount ? sel.anchorNode : null, v = "p";
    while (n && n !== surface) {
      if (n.nodeType === 1 && /^(H2|H3|H4|BLOCKQUOTE|PRE)$/.test(n.tagName)) { v = n.tagName.toLowerCase(); break; }
      n = n.parentNode;
    }
    style.value = v;
  };
  document.addEventListener("selectionchange", () => { if (surface.contains(getSelection().anchorNode)) refresh(); });

  const setMode = (m, quiet) => {
    if (m === mode) return;
    if (m === "source") { sync(); surface.hidden = true; bar.querySelectorAll("button.md-btn, select").forEach((x) => (x.disabled = true));
      area.hidden = false; if (!quiet) area.focus(); }
    else { surface.innerHTML = MD.toHtml(area.value, titles); area.hidden = true; surface.hidden = false;
      bar.querySelectorAll("button.md-btn, select").forEach((x) => (x.disabled = false)); if (!quiet) surface.focus(); }
    mode = m;
    modeVis.setAttribute("aria-pressed", String(m === "visual"));
    modeSrc.setAttribute("aria-pressed", String(m === "source"));
  };
  modeVis.addEventListener("click", () => setMode("visual"));
  modeSrc.addEventListener("click", () => setMode("source"));

  // typing Markdown at the start of a line formats it: "## " a heading, "- " a list, "> " a quote, "``` " code
  const SHORT = { "#": ["formatBlock", "<h2>"], "##": ["formatBlock", "<h2>"], "###": ["formatBlock", "<h3>"],
    "####": ["formatBlock", "<h4>"], "-": ["insertUnorderedList"], "*": ["insertUnorderedList"],
    "1.": ["insertOrderedList"], ">": ["formatBlock", "<blockquote>"], "```": ["formatBlock", "<pre>"] };
  surface.addEventListener("input", (ev) => {
    if (ev.inputType === "insertText" && ev.data === " ") {
      const sel = getSelection(), n = sel.anchorNode;
      if (n && n.nodeType === 3 && !inBlock("LI") && !inBlock("PRE")) {
        const before = n.nodeValue.slice(0, sel.anchorOffset).replace(/ /g, " ");
        const key = before.trim();
        const first = !n.previousSibling;
        if (first && before === key + " " && SHORT[key]) {
          n.nodeValue = n.nodeValue.slice(sel.anchorOffset);
          const r = document.createRange(); r.setStart(n, 0); r.collapse(true); sel.removeAllRanges(); sel.addRange(r);
          if (!n.nodeValue) n.nodeValue = "​";
          document.execCommand(SHORT[key][0], false, SHORT[key][1]);
        }
      }
    }
    later();
  });
  surface.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); openAsk("link"); }
    if (ev.key === "[" && /\[$/.test(textBefore())) { ev.preventDefault(); document.execCommand("delete"); openAsk("mention"); }
  });
  const textBefore = () => {
    const sel = getSelection(), n = sel.anchorNode;
    return n && n.nodeType === 3 ? n.nodeValue.slice(0, sel.anchorOffset) : "";
  };
  // a paste or a drop goes through Markdown: what can't be said in it keeps only its words
  const insertFrom = (dt) => {
    const html = dt.getData("text/html"), text = dt.getData("text/plain");
    let md = text;
    if (html) md = MD.toMd(new DOMParser().parseFromString(html, "text/html").body);
    if (!md) return;
    document.execCommand("insertHTML", false, MD.toHtml(md, titles));
    sync();
  };
  surface.addEventListener("paste", (ev) => { if (!ev.clipboardData) return; ev.preventDefault(); insertFrom(ev.clipboardData); });
  surface.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (!ev.dataTransfer) return;
    const at = document.caretRangeFromPoint ? document.caretRangeFromPoint(ev.clientX, ev.clientY) : null;
    if (at) { const s = getSelection(); s.removeAllRanges(); s.addRange(at); }
    insertFrom(ev.dataTransfer);
  });
  const form = area.form;
  if (form) form.addEventListener("submit", (ev) => {
    sync();
    if (required && !area.value.trim()) {
      ev.preventDefault();
      askHint.textContent = "";
      surface.classList.add("md-invalid");
      surface.setAttribute("aria-invalid", "true");
      if (mode === "visual") surface.focus(); else area.focus();
    }
  });
  if (document.documentElement.dataset.editor === "markdown") setMode("source", true);
  try { document.execCommand("defaultParagraphSeparator", false, "p"); document.execCommand("styleWithCSS", false, false); } catch (err) { /* older browsers */ }
}

// ------------------------------------------------------------------ the editor
// A workflow as its states and the ways out of them (practicehub/states.py). The left side lists where a record
// enters, each state with its work and its ways out, and the automations on each way; the right side edits the one
// selected. The hub writes the text (practicehub/web/workflow_editor.py): the model is sent to be checked, shown in
// the text tab, and proposed. A workflow in the old form (a list of steps) is never opened here, and never converted.
const TONES = { waiting: "default", active: "inprogress", attention: "moved", done: "success", ended: "removed" };
function editor(root) {
  const boot = JSON.parse(document.getElementById("wf-boot").textContent);
  const cat = boot.catalog, ro = !!boot.readonly;  // read only: the workflow's own page draws the graph, nothing more
  const list = (v) => (v ? (Array.isArray(v) ? v : [v]) : []);
  function norm(d) {  // the lists the text may have left out, or written as one table
    d.start = d.start && typeof d.start === "object" && !Array.isArray(d.start) ? d.start : {};
    d.start.do = list(d.start.do);
    d.state = list(d.state);
    d.edge = list(d.edge);
    for (const s of d.state) s.work = list(s.work);
    for (const e of d.edge) e.do = list(e.do);
    return d;
  }
  let wf = boot.data ? norm(boot.data) : null;
  let words = boot.words || {};  // each condition, in words (the hub says them)
  let sel = null;  // what the panel edits: { type: "start" | "state" | "work" | "edge" | "do", obj, owner }
  let tab = wf ? "visual" : "toml", lastSent = null, lastRes = null, timer = null, checking = 0;
  let sayers = [];  // the panel's "reads as" lines, refreshed when the hub says the conditions

  const canvas = root.querySelector("#wf-canvas"), panel = root.querySelector("#wf-panel");
  const tomlBox = root.querySelector("#wf-toml"), status = root.querySelector("#wf-status");
  if (status && !status.hasAttribute("aria-live")) status.setAttribute("aria-live", "polite");
  let dirty = false;  // unsaved changes: the page asks before it is left
  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });
  const form = root.querySelector("#wf-propose"), sourceIn = form && form.querySelector("input[name=source]");
  const modelIn = form && form.querySelector("input[name=model]");

  const human = (k) => { const t = String(k || "").replace(/[_-]+/g, " ").trim(); return t ? t[0].toUpperCase() + t.slice(1).toLowerCase() : t; };
  const stateById = (id) => wf.state.find((s) => s.id === id);
  const stateName = (id) => { const s = stateById(id); return s ? s.label || human(s.id) : id ? "a state that is not there" : "no state chosen"; };
  const edgeName = (e) => e.label || human(e.id);
  const roleName = (id) => (cat.roles.find((r) => r.id === id) || {}).name || human(id);
  const said = (c) => words[c] || "a condition holds";
  const stepTitle = (o) => o.title || human(o.id);
  const allSteps = () => [...wf.start.do, ...wf.state.flatMap((s) => s.work), ...wf.edge.flatMap((e) => e.do)];
  const outOf = (sid) => wf.edge.filter((e) => e.from === sid);
  const automated = (s) => s.work.length > 0 && s.work.every((w) => w.kind === "automation" || w.kind === "wait");
  const kindOf = (type, o) => (type === "do" ? "automation" : o.kind || "task");
  const agentsMay = (o, kind) => (kind === "task" || kind === "gate") && !o.persons_only && !(kind === "gate" && o.signer === "person");
  const dur = (d) => { const m = /^(\d+)([mhdw])$/.exec(String(d || "").trim()); if (!m) return String(d || "");
    return `${m[1]} ${{ m: "minute", h: "hour", d: "day", w: "week" }[m[2]]}${m[1] === "1" ? "" : "s"}`; };
  const timingOf = (s) => [s.for ? "waits " + dur(s.for) : "", s.at ? "waits until a set time" : "",
    s.until ? "waits until " + said(s.until) : "", s.timeout ? "gives up after " + dur(s.timeout) : "",
    s.due ? "due in " + dur(s.due) : ""].filter(Boolean).join("; ");

  // ---- the states, as the workflow's page shows them, each part a button that selects it
  function choose(type, obj, owner) { sel = obj ? { type, obj, owner } : null; redraw(); renderPanel(); }
  // ---- the workflow as a graph: each state a box with its work, each automation a box of its own on the way (it is
  // a stop the record passes through), each way out an arrow, named on its first leg. Select anything to change it;
  // drag from a state's handle onto another state to add a way out between them.
  const SW = 230, AW = 180, AH = 46, GX = 28, GY = 58, PAD = 20, LINE = 19, LANE = 16;
  function nodes() {
    const ns = new Map(), add = (n) => (ns.set(n.id, n), n);
    add({ id: "@start", kind: "start", obj: wf.start, w: 96, h: 40 });
    for (const s of wf.state) add({ id: "s:" + s.id, kind: "state", obj: s, w: SW, h: 52 + LINE * Math.max(1, s.work.length) });
    const chains = [];
    const chain = (e, type, from) => {
      const ids = [from];
      e.do.forEach((a, i) => { add({ id: `a:${type}:${wf.edge.indexOf(e)}:${i}`, kind: "do", obj: a, owner: e, w: AW, h: AH }); ids.push(`a:${type}:${wf.edge.indexOf(e)}:${i}`); });
      if (e.to && stateById(e.to)) ids.push("s:" + e.to);
      chains.push({ e, type, ids });
    };
    chain(wf.start, "start", "@start");
    for (const e of wf.edge) if (stateById(e.from)) chain(e, "edge", "s:" + e.from);
    return { ns, chains };
  }
  function layout(ns, chains) {
    const out = new Map([...ns.keys()].map((k) => [k, []]));
    const link = (a, b) => { if (a !== b && out.has(a) && out.has(b)) out.get(a).push(b); };
    for (const c of chains) for (let i = 1; i < c.ids.length; i++) link(c.ids[i - 1], c.ids[i]);
    for (const n of ns.values()) if (n.kind === "do") { if (n.obj.to && stateById(n.obj.to)) link(n.id, "s:" + n.obj.to); if (n.obj.on_fail && stateById(n.obj.on_fail)) link(n.id, "s:" + n.obj.on_fail); }
    // the ways back (to a state already on the path) are not counted: what is left is drawn left to right
    const back = new Set(), seen = new Set(), stack = new Set(), order = [];
    const dfs = (v) => { seen.add(v); stack.add(v); for (const w of out.get(v)) { if (stack.has(w)) back.add(v + ">" + w); else if (!seen.has(w)) dfs(w); } stack.delete(v); order.push(v); };
    dfs("@start");
    for (const k of ns.keys()) if (!seen.has(k)) dfs(k);
    const rank = new Map();
    for (const v of order.reverse()) { if (!rank.has(v)) rank.set(v, 0); for (const w of out.get(v)) if (!back.has(v + ">" + w)) rank.set(w, Math.max(rank.get(w) || 0, rank.get(v) + 1)); }
    // top to bottom: each rank a row; a row is ordered by where what leads into it sits, so arrows cross less
    const rows = [], preds = new Map([...ns.keys()].map((k) => [k, []]));
    for (const [v, ws] of out) for (const w of ws) if (!back.has(v + ">" + w)) preds.get(w).push(v);
    for (const v of order) (rows[rank.get(v)] = rows[rank.get(v)] || []).push(v);
    const at = new Map();
    rows.forEach((r) => {
      if (!r) return;
      const bary = (v) => { const ps = preds.get(v).filter((u) => at.has(u)); return ps.length ? ps.reduce((t, u) => t + at.get(u), 0) / ps.length : 1e9; };
      r.sort((a, b) => bary(a) - bary(b));
      const n = r.length; r.forEach((v, i) => at.set(v, (i + 0.5) / n));
    });
    const rowW = rows.map((r) => (r || []).reduce((t, v) => t + ns.get(v).w, 0) + Math.max(0, (r || []).length - 1) * GX);
    const W = Math.max(320, ...rowW);
    let y = PAD;
    rows.forEach((r, i) => {
      if (!r) return;
      let x = PAD + (W - rowW[i]) / 2;
      const h = Math.max(...r.map((v) => ns.get(v).h));
      for (const v of r) { const n = ns.get(v); n.x = x; n.y = y + (h - n.h) / 2; x += n.w + GX; }
      y += h + GY;
    });
    return { back, width: W + 2 * PAD, height: y - GY + PAD };
  }
  function redraw() {
    canvas.textContent = "";
    if (!wf) return;
    const { ns, chains } = nodes(), { back, width, height } = layout(ns, chains);
    let lanes = 0;
    const backs = chains.reduce((t, c) => t + c.ids.filter((v, i) => i && back.has(c.ids[i - 1] + ">" + v)).length, 0);
    const Wd = width + backs * LANE + (backs ? 90 : 0), H = height;
    const svg = sv("svg", { class: "wf-graph", viewBox: `0 0 ${Wd} ${H}`, width: Wd, height: H,
      role: "group", "aria-label": "The workflow as a graph: its states, the automations on the way, and the ways out" });
    const mid = "wfa" + (++flowSeq), defs = sv("defs");
    for (const [k, cls] of [["", "fl-arrowhead"], ["-f", "fl-arrowhead fail"], ["-s", "fl-arrowhead sel"]]) {
      const m = sv("marker", { id: mid + k, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
      m.append(sv("path", { d: "M0 0L10 5L0 10z", class: cls })); defs.append(m);
    }
    svg.append(defs);
    const edgesG = sv("g"), labelsG = sv("g"), nodesG = sv("g");
    svg.append(edgesG, nodesG, labelsG);
    const isSel = (o) => !!sel && sel.obj === o;
    const act = (g, label, fn) => {
      if (ro) return;
      g.setAttribute("tabindex", "0"); g.setAttribute("role", "button"); g.setAttribute("aria-label", label);
      g.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); } });
    };
    function arrow(a, b, o) {  // from node a to node b: forward left to right, a way back under everything
      const A = ns.get(a), B = ns.get(b);
      let d, lx, ly;
      if (o.back) {  // up a lane of its own on the right, into the side of the state it goes back to
        const x = width + 8 + LANE * lanes++, y1 = A.y + A.h / 2, y2 = B.y + B.h / 2 + 8, x1 = A.x + A.w, x2 = B.x + B.w + 2;
        d = `M${x1} ${y1}H${x - 8}Q${x} ${y1} ${x} ${y1 - 8}V${y2 + 8}Q${x} ${y2} ${x - 8} ${y2}H${x2}`;
        lx = x; ly = (y1 + y2) / 2;
      } else {
        const x1 = A.x + A.w / 2, y1 = A.y + A.h, x2 = B.x + B.w / 2, y2 = B.y - 2, c = Math.max(20, (y2 - y1) / 2);
        d = `M${x1} ${y1}C${x1} ${y1 + c} ${x2} ${y2 - c} ${x2} ${y2}`;
        // its name in the gap under where it leaves (a point on the curve), never over a box further down
        const near = Math.min(0.5, (GY * 0.8) / Math.max(1, y2 - y1)), t = o.atEnd ? 1 - near : near, u = 1 - t;  // or just above where it arrives
        lx = u * u * u * x1 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x2;
        ly = u * u * u * y1 + 3 * u * u * t * (y1 + c) + 3 * u * t * t * (y2 - c) + t * t * t * y2;
      }
      const p = sv("path", { d, class: "fl-edge" + (o.cond ? " cond" : "") + (o.fail ? " fail" : "") + (o.sel ? " sel" : ""),
        "marker-end": `url(#${mid}${o.fail ? "-f" : o.sel ? "-s" : ""})` });
      edgesG.append(p);
      return [lx, ly];
    }
    const placed = [];  // the labels drawn so far: a new one moves down its arrow's side until it is clear of them, and of the boxes
    const hits = (x, y, w) => placed.some((r) => Math.abs(r[0] - x) < (r[2] + w) / 2 + 4 && Math.abs(r[1] - y) < 22)
      || [...ns.values()].some((b) => x + w / 2 > b.x - 2 && x - w / 2 < b.x + b.w + 2 && y + 10 > b.y - 2 && y - 10 < b.y + b.h + 2);
    function label(x, y, text, o) {
      const t = clip(text, 26), w = 16 + t.length * 6.8;
      for (let i = 0; i < 12 && hits(x, y, w); i++) x += (i % 2 ? -1 : 1) * (i + 1) * (w / 2 + 6);  // beside, then the other side, wider
      x = Math.max(w / 2 + 2, Math.min(Wd - w / 2 - 2, x));
      placed.push([x, y, w]);
      const g = sv("g", { class: "wf-elabel" + (o.sel ? " sel" : "") + (o.fail ? " fail" : "") + (o.muted ? " muted" : "") });
      g.append(sv("title", {}, o.title || text));
      g.append(sv("rect", { x: x - w / 2, y: y - 10, width: w, height: 20, rx: 10 }));
      g.append(sv("text", { x, y: y + 4, "text-anchor": "middle" }, t));
      if (o.pick) act(g, o.title || text, o.pick);
      labelsG.append(g);
    }
    for (const c of chains) {
      const e = c.e, from = c.type === "start" ? null : stateById(e.from), auto = !!from && automated(from);
      const cond = c.type !== "start" && !!e.when, on = isSel(e);
      const name = c.type === "start" ? e.label || "Filed" : edgeName(e);
      const tip = c.type === "start" ? "where a record enters" : name + (e.when ? (auto ? " — when " : " — offered only when ") + said(e.when) : auto ? " — otherwise" : "");
      const pickE = () => choose(c.type === "start" ? "start" : "edge", e);
      if (c.ids.length === 1) {  // nowhere to go yet: a stub, still named and selectable
        const A = ns.get(c.ids[0]), x1 = A.x + A.w / 2, y1 = A.y + A.h;
        edgesG.append(sv("path", { d: `M${x1} ${y1}v34`, class: "fl-edge cond" + (on ? " sel" : "") }));
        if (c.type !== "start") label(x1, y1 + 34, name + " → ?", { sel: on, title: tip + " (choose where it goes)", pick: pickE });
        continue;
      }
      for (let i = 1; i < c.ids.length; i++) {
        const [lx, ly] = arrow(c.ids[i - 1], c.ids[i], { back: back.has(c.ids[i - 1] + ">" + c.ids[i]), cond, sel: on });
        if (i === 1 && c.type !== "start") label(lx, ly, name, { sel: on, title: tip, pick: pickE });
      }
    }
    for (const n of ns.values()) if (n.kind === "do") {  // an automation that sends the record on, or back when it fails
      for (const [k, fail] of [["to", false], ["on_fail", true]]) {
        if (!n.obj[k] || !stateById(n.obj[k])) continue;
        const b = "s:" + n.obj[k], [lx, ly] = arrow(n.id, b, { back: back.has(n.id + ">" + b), fail, cond: true, atEnd: true });
        label(lx, ly, fail ? "if it fails" : "sends it on", { fail, muted: !fail, title: (fail ? "if it fails, to " : "and sends it to ") + stateName(n.obj[k]) });
      }
    }
    for (const n of ns.values()) {
      const g = sv("g", { class: `wf-node k-${n.kind}` + (isSel(n.obj) ? " sel" : ""), transform: `translate(${n.x} ${n.y})` });
      if (n.kind === "start") {
        g.append(sv("rect", { width: n.w, height: n.h, rx: n.h / 2, class: "box" }));
        g.append(sv("text", { x: n.w / 2, y: n.h / 2 + 4, "text-anchor": "middle" }, "Enters"));
        act(g, "Where a record enters", () => choose("start", wf.start));
      } else if (n.kind === "do") {
        const a = n.obj, kind = a.block ? ((cat.blocks || {})[a.block] || {}).title || "a block" : a.action || "automation";
        g.append(sv("title", {}, `${stepTitle(a)} — done automatically (${kind})${a.when ? ", only when " + said(a.when) : ""}`));
        g.append(sv("rect", { width: n.w, height: n.h, rx: 8, class: "box" }));
        g.append(sv("rect", { x: 10, y: 13, width: 20, height: 20, rx: 3, class: "ki-box" }));
        g.append(sv("text", { x: 20, y: 27, "text-anchor": "middle", class: "ki-t" }, "A"));
        g.append(sv("text", { x: 38, y: 20 }, clip(stepTitle(a), 21)));
        g.append(sv("text", { x: 38, y: 36, class: "s" }, clip(a.when ? "only when " + said(a.when) : kind, 24)));
        act(g, "Automation: " + stepTitle(a), () => choose("do", a, n.owner));
      } else {
        const s = n.obj, tone = TONES[s.tone] || "default";
        g.setAttribute("data-state", s.id);
        g.append(sv("title", {}, `${stateName(s.id)}${s.help ? " — " + s.help : ""}`));
        g.append(sv("rect", { width: n.w, height: n.h, rx: 6, class: "box" }));
        g.append(sv("rect", { width: n.w, height: 6, rx: 3, class: "tone t-" + tone }));
        const head = sv("g", { class: "wf-head" });
        head.append(sv("rect", { x: 2, y: 8, width: n.w - 4, height: 26, rx: 3, class: "hit" }));
        head.append(sv("text", { x: 12, y: 26, class: "name" }, clip(stateName(s.id), 26)));
        head.append(sv("text", { x: n.w - 10, y: 26, class: "s", "text-anchor": "end" }, automated(s) ? "automated" : outOf(s.id).length ? "" : "ends here"));
        act(head, "State: " + stateName(s.id), () => choose("state", s));
        g.append(head);
        if (!s.work.length) g.append(sv("text", { x: 12, y: 52, class: "s" }, "No work here: it waits"));
        s.work.forEach((w, i) => {
          const kind = kindOf("work", w), ki = kind === "gate" ? ["gate", "D"] : kind === "task" ? ["task", "T"] : ["automation", "A"];
          const row = sv("g", { class: "wf-row" + (isSel(w) ? " sel" : ""), transform: `translate(0 ${38 + LINE * i})` });
          row.append(sv("title", {}, stepTitle(w) + (timingOf(w) ? " — " + timingOf(w) : "")));
          row.append(sv("rect", { x: 4, y: 0, width: n.w - 8, height: LINE - 1, rx: 3, class: "hit" }));
          row.append(sv("rect", { x: 10, y: 3, width: 13, height: 13, rx: 2, class: "ki-box k-" + ki[0] }));
          row.append(sv("text", { x: 16.5, y: 13, "text-anchor": "middle", class: "ki-t" }, ki[1]));
          const who = kind === "task" || kind === "gate" ? (w.role ? roleName(w.role) : "") : kind === "wait" ? timingOf(w) || "waits" : "";
          row.append(sv("text", { x: 29, y: 13, class: "w" }, clip(stepTitle(w) + (who ? " · " + who : ""), 30)));
          if (agentsMay(w, kind)) row.append(sv("text", { x: n.w - 12, y: 13, class: "ai-t2", "text-anchor": "end" }, "AI"));
          act(row, `${kind === "gate" ? "Decision" : kind === "task" ? "Task" : "Automation"}: ${stepTitle(w)}`, () => choose("work", w, s));
          g.append(row);
        });
        if (!ro) {
          const hd = sv("circle", { cx: n.w - 14, cy: n.h, r: 7, class: "wf-handle" });
          hd.append(sv("title", {}, "Drag onto a state to add a way out to it"));
          hd.addEventListener("pointerdown", (ev) => connect(ev, s, n, svg));
          hd.addEventListener("click", (ev) => ev.stopPropagation());
          g.append(hd);
        }
      }
      nodesG.append(g);
    }
    canvas.append(svg);
  }
  function connect(ev, s, n, svg) {  // drag from a state's handle: a way out, to the state it is dropped on
    ev.preventDefault(); ev.stopPropagation();
    const pt = (e) => { const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal; return [(e.clientX - r.left) * vb.width / r.width, (e.clientY - r.top) * vb.height / r.height]; };
    const x0 = n.x + n.w - 14, y0 = n.y + n.h;
    const line = sv("path", { class: "fl-edge sel wf-drag", d: `M${x0} ${y0}L${x0} ${y0}` });
    svg.append(line);
    const move = (e) => { const [x, y] = pt(e); line.setAttribute("d", `M${x0} ${y0}L${x} ${y}`); };
    const up = (e) => {
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      line.remove();
      const hit = document.elementFromPoint(e.clientX, e.clientY), g = hit && hit.closest && hit.closest("[data-state]");
      if (!g) return;
      const to = g.getAttribute("data-state");
      const edge = { id: fresh("way", new Set(wf.edge.map((x) => x.id))), label: to === s.id ? "Again" : "To " + stateName(to), from: s.id, to, do: [] };
      const last = wf.edge.map((x) => x.from).lastIndexOf(s.id);
      wf.edge.splice(last < 0 ? wf.edge.length : last + 1, 0, edge);
      sel = { type: "edge", obj: edge }; changed(true);
    };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
  }
  if (ro) { redraw(); return; }
  function changed(rebuild) {
    dirty = true;
    redraw();
    if (rebuild) renderPanel();
    schedule();
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(check, 500); }

  async function check(force) {
    const body = JSON.stringify(tab === "toml" || !wf ? { source: tomlBox.value } : { model: wf });
    if (body === lastSent && !force) return lastRes;
    lastSent = body;
    const mine = ++checking;
    status.textContent = "Checking…";
    try {
      const r = await fetch("/api/workflows/check", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body });
      const res = await r.json();
      if (mine !== checking) return res;
      lastRes = res;
      const fresh = Object.keys(res.words || {}).some((c) => words[c] !== res.words[c]);
      words = Object.assign({}, words, res.words || {});
      if (fresh) { redraw(); for (const [n, get] of sayers) n.textContent = get(); }
      showProblems(res.problems || [res.error || "could not check"]);
      return res;
    } catch (e) {
      status.textContent = "Could not reach the hub to check the workflow.";
    }
  }
  function showProblems(problems) {
    status.textContent = "";
    if (!problems.length) {
      status.append(el("span", { class: "loz success" }, "ready"), " The hub accepts this workflow. Say what changes and propose it.");
    } else {
      status.append(el("span", { class: "loz removed" }, problems.length + (problems.length === 1 ? " problem" : " problems")),
        el("ul", { class: "ed-problems" }, problems.map((p) => el("li", {}, p))));
    }
  }

  // ---- small bound controls
  function text(label, obj, k, o) {
    o = o || {};
    const attrs = { value: obj[k] === undefined || obj[k] === null ? "" : String(obj[k]), placeholder: o.placeholder || "",
      oninput: (ev) => { obj[k] = o.number ? (ev.target.value === "" ? undefined : Number(ev.target.value)) : ev.target.value; changed(false); } };
    if (o.onchange) { delete attrs.oninput; attrs.onchange = (ev) => o.onchange(ev.target.value); }
    const ctl = o.area ? el("textarea", Object.assign({ rows: o.rows || 3 }, attrs)) : el("input", Object.assign({ type: o.number ? "number" : "text" }, attrs));
    return el("label", {}, label, ctl, o.help ? el("small", {}, o.help) : null, o.says ? saysLine(obj, k) : null);
  }
  function saysLine(obj, k) {  // a condition, in words, under the box it is typed in
    const get = () => (obj[k] ? "Reads as: " + said(obj[k]) : "");
    const n = el("small", { class: "wf-says" }, get());
    sayers.push([n, get]);
    return n;
  }
  function select(label, obj, k, options, o) {
    o = o || {};
    const s = el("select", { onchange: (ev) => { obj[k] = ev.target.value || undefined; if (o.after) o.after(); changed(!!o.rebuild); } },
      o.blank !== undefined ? el("option", { value: "" }, o.blank) : null,
      options.map(([v, t]) => el("option", { value: v, selected: String(obj[k] ?? "") === String(v) ? "selected" : null }, t)));
    return el("label", {}, label, s, o.help ? el("small", {}, o.help) : null);
  }
  function checks(label, obj, k, options, o) {
    o = o || {};
    const cur = new Set(obj[k] || []);
    const box = el("div", { class: "checks" }, options.length ? options.map(([v, t]) => el("label", {},
      el("input", { type: "checkbox", checked: cur.has(v), onchange: (ev) => {
        const now = new Set(obj[k] || []); ev.target.checked ? now.add(v) : now.delete(v);
        obj[k] = options.map(([x]) => x).filter((x) => now.has(x)).concat([...now].filter((x) => !options.some(([y]) => y === x)));
        changed(false); } }), t)) : el("span", { class: "muted" }, o.none || "none"));
    return el("label", {}, label, box, o.help ? el("small", {}, o.help) : null);
  }
  function csv(label, obj, k, o) {
    o = o || {};
    return el("label", {}, label, el("input", { type: "text", value: (obj[k] || []).join(", "), placeholder: o.placeholder || "",
      oninput: (ev) => { obj[k] = ev.target.value.split(",").map((x) => x.trim()).filter(Boolean); changed(false); } }),
      o.help ? el("small", {}, o.help) : null);
  }
  function bool(label, obj, k, o) {
    return el("label", { class: "radio" }, el("input", { type: "checkbox", checked: !!obj[k],
      onchange: (ev) => { obj[k] = ev.target.checked; changed(!!(o && o.rebuild)); } }), label);
  }
  const TYPES = ["string", "text", "number", "boolean", "choice", "list", "date", "datetime", "member"];
  function fieldsEditor(obj, k, title, help) {
    const wrap = el("div");
    function draw() {
      wrap.textContent = "";
      const rows = (obj[k] || []).map((f, i) => el("tr", {},
        el("td", {}, el("input", { value: f.name || "", placeholder: "name", "aria-label": "field name",
          oninput: (ev) => { f.name = ev.target.value; changed(false); } })),
        el("td", {}, el("select", { "aria-label": "field type", onchange: (ev) => { f.type = ev.target.value; changed(false); draw(); } },
          TYPES.map((t) => el("option", { value: t, selected: (f.type || "string") === t ? "selected" : null }, t)))),
        el("td", { title: "required" }, el("input", { type: "checkbox", "aria-label": "required", checked: !!f.required,
          onchange: (ev) => { f.required = ev.target.checked; changed(false); } })),
        el("td", {}, f.type === "choice" && !f.choices_from
          ? el("input", { value: (f.choices || []).join(", "), placeholder: "a, b, c", "aria-label": "choices",
              oninput: (ev) => { f.choices = ev.target.value.split(",").map((x) => x.trim()).filter(Boolean); changed(false); } })
          : el("input", { value: f.label || "", placeholder: "label", "aria-label": "field label",
              oninput: (ev) => { f.label = ev.target.value; changed(false); } })),
        el("td", {}, el("button", { type: "button", class: "subtle", title: "remove", "aria-label": "remove field",
          onclick: () => { obj[k].splice(i, 1); changed(false); draw(); } }, "×"))));
      wrap.append(el("table", { class: "ed-fields" },
        rows.length ? el("tr", {}, el("th", {}, "Name"), el("th", {}, "Type"), el("th", {}, "Req."), el("th", {}, "Label / choices"), el("th")) : null,
        rows),
        el("button", { type: "button", class: "secondary", onclick: () => {
          obj[k] = obj[k] || []; obj[k].push({ name: "", type: "string" }); changed(false); draw(); } }, "+ Add field"));
    }
    draw();
    return el("fieldset", {}, el("legend", {}, title), help ? el("small", { class: "muted" }, help) : null, wrap);
  }

  // ---- blocks
  const blocksFor = (kind) => Object.values(cat.blocks || {}).filter((b) => b.kind === kind);
  function chooseBlock(s, kind, bid) {
    if (bid) {
      const b = cat.blocks[bid];
      s.block = bid;
      delete s.action; delete s.skill; delete s.fields;
      const params = {};
      for (const f of b.inputs) if (s.params && s.params[f.name] !== undefined) params[f.name] = s.params[f.name];
        else if (f.required && (f.default === undefined || f.default === null)) params[f.name] = "";  // the catalog sends null
      s.params = params;
    } else {
      delete s.block;
      if (kind === "automation") { s.action = "message.send"; s.params = {}; }
      else { delete s.params; s.fields = [{ name: "done", type: "boolean", label: "Done" }]; }
    }
    changed(true);
  }
  function paramValue(f, raw) {
    if (raw === "") return undefined;
    if (raw.includes("{{")) return raw;  // read from the run
    if (f.type === "number" && raw.trim() !== "" && !isNaN(Number(raw))) return Number(raw);
    if (f.type === "boolean" && (raw === "true" || raw === "false")) return raw === "true";
    return raw;
  }
  function paramsEditor(s, b) {
    s.params = s.params || {};
    const rows = b.inputs.map((f) => {
      const cur = s.params[f.name];
      const ph = f.default !== undefined && f.default !== null ? `default: ${f.default}` : (f.choices ? f.choices.join(" | ") : "");
      return el("label", {}, el("span", {}, el("code", {}, f.name), " ", el("span", { class: "muted" }, f.type + (f.required ? ", required" : ""))),
        el("input", { type: "text", value: cur === undefined ? "" : String(cur), placeholder: ph,
          oninput: (ev) => { const v = paramValue(f, ev.target.value); if (v === undefined) delete s.params[f.name]; else s.params[f.name] = v; changed(false); } }),
        f.help ? el("small", {}, f.help) : null);
    });
    const tip = b.kind === "task"
      ? "A plain value is written into the agent's prompt. A value in double braces, e.g. {{sort.reason}}, is read from the run and stays data: the work must also read it (What it reads)."
      : "A plain value, or one read from the run in double braces, e.g. {{run.record}} or {{sort.reason}}.";
    return el("fieldset", {}, el("legend", {}, "Block parameters"), ...rows, el("small", { class: "muted" }, tip),
      el("p", {}, el("a", { href: "/blocks/" + b.id, target: "_blank" }, "Open the block"),
        el("span", { class: "muted" }, ` · returns ${b.outputs.join(", ") || "the fields of " + b.capability}` +
          (b.kind === "task" ? ` · agents need ${b.capability} in their profile` : ""),
          b.active ? "" : " · not approved yet: the workflow cannot be approved until it is")));
  }

  // ---- adding, renaming and removing
  const ID = /^[a-z0-9]+(-[a-z0-9]+)*$/;
  function fresh(prefix, taken) { let n = 1, id; do { id = `${prefix}-${n++}`; } while (taken.has(id)); return id; }
  const stepIds = () => new Set(allSteps().map((o) => o.id));
  function addState() {
    const s = { id: fresh("state", new Set(wf.state.map((x) => x.id))), label: "New state", work: [] };
    wf.state.push(s);
    if (!wf.start.to && !wf.start.do.length) wf.start.to = s.id;
    sel = { type: "state", obj: s }; changed(true);
  }
  function addWork(s) {
    const kind = automated(s) ? "automation" : "task";
    const role = cat.roles.length ? cat.roles[0].id : undefined;
    const w = { id: fresh("work", stepIds()), title: kind === "task" ? "New task" : "Do it automatically", kind };
    if (kind === "task") Object.assign(w, { role, risk: "low", fields: [{ name: "done", type: "boolean", label: "Done" }] });
    else Object.assign(w, { action: "message.send", params: {} });
    s.work.push(w);
    sel = { type: "work", obj: w, owner: s }; changed(true);
  }
  function addEdge(s) {
    const i = wf.state.indexOf(s), other = wf.state[i + 1] || wf.state.find((x) => x !== s) || s;
    const e = { id: fresh("way", new Set(wf.edge.map((x) => x.id))), label: "Move on", from: s.id, to: other.id, do: [] };
    const last = wf.edge.map((x) => x.from).lastIndexOf(s.id);  // with the others out of the same state
    wf.edge.splice(last < 0 ? wf.edge.length : last + 1, 0, e);
    sel = { type: "edge", obj: e }; changed(true);
  }
  function addDo(e) {
    const a = { id: fresh("auto", stepIds()), title: "Do it automatically", action: "message.send", params: {} };
    e.do.push(a);
    sel = { type: "do", obj: a, owner: e }; changed(true);
  }
  function fixId(v, what) {
    v = v.trim();
    if (!ID.test(v) || v.includes("--")) { status.textContent = `${what}: lower-case letters, digits and single dashes.`; return null; }
    return v;
  }
  function renameState(s, v) {
    const nid = fixId(v, "A state's id"), old = s.id;
    if (!nid || nid === old) return renderPanel();
    s.id = nid;
    if (wf.start.to === old) wf.start.to = nid;
    for (const e of wf.edge) { if (e.from === old) e.from = nid; if (e.to === old) e.to = nid; }
    for (const a of allSteps()) {
      for (const k of ["to", "on_fail"]) if (a[k] === old) a[k] = nid;
      if (Array.isArray(a.moves)) a.moves = a.moves.map((m) => (m === old ? nid : m));
    }
    changed(true);
  }
  function renameStep(s, v) {
    const nid = fixId(v, "An id"), old = s.id;
    if (!nid || nid === old) return renderPanel();
    s.id = nid;
    const re = new RegExp("(?<![A-Za-z0-9_-])" + old.replace(/-/g, "\\-") + "\\.", "g");
    for (const o of [...allSteps(), ...wf.edge]) {
      for (const k of ["after", "not_by"]) if (Array.isArray(o[k])) o[k] = o[k].map((a) => (a === old ? nid : a));
      if (o.review_of === old) o.review_of = nid;
      if (o.context) o.context = o.context.map((c) => (c === old ? nid : c.startsWith(old + ".") ? nid + c.slice(old.length) : c));
      for (const f of ["when", "agent_criteria", "until", "at"]) if (typeof o[f] === "string") o[f] = o[f].replace(re, nid + ".");
      if (o.params) o.params = JSON.parse(JSON.stringify(o.params).replace(new RegExp("\\{\\{\\s*" + old + "\\.", "g"), "{{" + nid + "."));
    }
    changed(true);
  }
  function renameEdge(e, v) {
    const nid = fixId(v, "A way out's id"), old = e.id;
    if (!nid || nid === old) return renderPanel();
    e.id = nid;
    // a condition that reads the choice: <work>.outcome == '<edge>'
    const re = new RegExp("(\\.outcome\\s*[!=]=\\s*)(['\"])" + old.replace(/-/g, "\\-") + "\\2", "g");
    for (const o of [...allSteps(), ...wf.edge]) for (const f of ["when", "agent_criteria"])
      if (typeof o[f] === "string") o[f] = o[f].replace(re, (m, a, q) => a + q + nid + q);
    changed(true);
  }
  function forget(id) {  // what pointed at a removed piece of work or automation
    for (const o of allSteps()) {
      for (const k of ["after", "not_by"]) if (Array.isArray(o[k])) o[k] = o[k].filter((a) => a !== id);
      if (o.review_of === id) delete o.review_of;
      if (o.context) o.context = o.context.filter((c) => c !== id && !c.startsWith(id + "."));
    }
  }
  async function remove(trigger) {
    const { type, obj, owner } = sel;
    const what = { state: `the state "${stateName(obj.id)}", its work and every way into or out of it`,
      work: `the work "${stepTitle(obj)}"`, edge: `the way out "${edgeName(obj)}" and its automations`,
      do: `the automation "${stepTitle(obj)}"` }[type];
    const ok = await askConfirm(trigger, `Remove ${what}?`, { danger: true, title: "Remove this?", label: "Remove" });
    if (!ok) return;
    if (type === "state") {
      for (const w of obj.work) forget(w.id);
      for (const e of wf.edge.filter((x) => x.from === obj.id || x.to === obj.id)) for (const a of e.do) forget(a.id);
      wf.state = wf.state.filter((x) => x !== obj);
      wf.edge = wf.edge.filter((x) => x.from !== obj.id && x.to !== obj.id);
      if (wf.start.to === obj.id) delete wf.start.to;
      for (const a of allSteps()) for (const k of ["to", "on_fail"]) if (a[k] === obj.id) delete a[k];
    } else if (type === "edge") {
      for (const a of obj.do) forget(a.id);
      wf.edge = wf.edge.filter((x) => x !== obj);
    } else {
      const items = type === "work" ? owner.work : owner.do;
      items.splice(items.indexOf(obj), 1);
      forget(obj.id);
    }
    sel = null; changed(true);
  }

  // ---- panels
  const put = (...k) => panel.append(...k.filter((x) => x !== null && x !== undefined && x !== false));
  const stateOptions = () => wf.state.map((s) => [s.id, stateName(s.id)]);
  function heading(kind, title) {
    return el("h2", {}, kind ? el("span", { class: `ki ${kind[0]}` }, kind[1]) : null, title, el("span", { class: "sp" }),
      el("button", { type: "button", class: "subtle", onclick: () => choose(null) }, "Workflow settings"));
  }
  const removeButton = (label) => el("p", {}, el("button", { type: "button", class: "danger", onclick: (ev) => remove(ev.currentTarget) }, label));
  function renderPanel() {
    panel.textContent = "";
    sayers = [];
    if (!wf) return;
    if (!sel) return workflowPanel();
    ({ start: startPanel, state: statePanel, edge: edgePanel, work: stepPanel, do: stepPanel })[sel.type](sel.obj, sel.owner);
  }
  function startPanel(st) {
    put(heading(null, "Where a record enters"),
      select("Enters in", st, "to", stateOptions(), { blank: "no state: it only runs its automations", rebuild: true,
        help: "the state a new record starts in, and one the workflow starts on again" }),
      text("Called", st, "label", { placeholder: "Filed", help: "what the record's history calls its arrival, e.g. Filed by the lab" }),
      el("p", {}, el("button", { type: "button", class: "secondary", onclick: () => addDo(st) }, "+ Automation on the way in")),
      el("p", { class: "muted" }, "Its automations run as the record enters. Select one on the left to change it."));
  }
  function statePanel(s) {
    put(heading(null, "State"),
      text("Name", s, "label", { help: "what the record page shows" }),
      text("Id", s, "id", { onchange: (v) => renameState(s, v), help: "the value its record's state field takes: lower-case letters, digits and dashes" }),
      select("Tone", s, "tone", [["waiting", "waiting"], ["active", "being worked"], ["attention", "needs attention"], ["done", "done"], ["ended", "ended"]],
        { blank: "plain", help: "how the state reads at a glance" }),
      text("Help", s, "help", { area: true, rows: 2, help: "shown on the record page" }),
      el("p", { class: "muted" }, automated(s) ? "Its work is automated, so the record leaves by the first way out whose condition holds."
        : "Its work is a person's (an agent may do a task). A state is worked by people or automated, never both."),
      el("p", {}, el("button", { type: "button", class: "secondary", onclick: () => addWork(s) }, "+ Work"), " ",
        el("button", { type: "button", class: "secondary", onclick: () => addEdge(s) }, "+ Way out")),
      removeButton("Remove state"));
  }
  function edgePanel(e) {
    const from = stateById(e.from), auto = !!from && automated(from);
    put(heading(null, "Way out"),
      text("Label", e, "label", { help: "the button the person presses, and the history's word for the move" }),
      text("Id", e, "id", { onchange: (v) => renameEdge(e, v), help: "lower-case letters, digits and single dashes" }),
      select("From", e, "from", stateOptions(), { blank: "choose a state", rebuild: true }),
      select("To", e, "to", stateOptions(), { blank: "choose a state", rebuild: true }),
      text(auto ? "When" : "Offered only when", e, "when", { placeholder: "e.g. check.band in ['critical']", says: true,
        help: auto ? "the record leaves this way when it holds; the last way out may go without, as the otherwise"
          : "a condition on earlier work; without one it is always offered" }),
      text("Help", e, "help", { area: true, rows: 2, help: "shown beside the button" }),
      el("p", {}, el("button", { type: "button", class: "secondary", onclick: () => addDo(e) }, "+ Automation on the way")),
      removeButton("Remove way out"));
  }
  function stepPanel(s, owner) {
    const type = sel.type, kind = kindOf(type, s);
    const others = allSteps().filter((o) => o !== s);
    const roles = cat.roles.map((r) => [r.id, r.name]);
    const ki = kind === "gate" ? ["gate", "D"] : kind === "task" ? ["task", "T"] : ["automation", "A"];
    const where = type === "do" ? (owner === wf.start ? "On the way in" : `On the way "${edgeName(owner)}"`) : `Work in "${stateName(owner.id)}"`;
    put(heading(ki, where),
      text("Title", s, "title"),
      text("Id", s, "id", { onchange: (v) => renameStep(s, v), help: "lower-case letters, digits and dashes; later work reads its answers by it" }),
      type === "work" ? select("Kind", s, "kind", [["task", "Task: someone fills in a form"], ["gate", "Decision: someone signs which way out"],
        ["automation", "Automation: a deterministic action"], ["wait", "Wait: until something holds, or for a time"]],
        { rebuild: true, help: "a state is worked by people (tasks, decisions) or automated (automations, waits), never both",
          after: () => { if (s.kind === "automation" && !s.action && !s.block) { s.action = "message.send"; s.params = {}; } } }) : null,
      kind === "task" || kind === "gate" ? select("Role", s, "role", roles, { blank: "choose a role", help: "work names roles, never people" }) : null,
      text("Only when", s, "when", { placeholder: "e.g. check.band in ['critical']", says: true,
        help: type === "do" ? "it runs on this way only when this holds" : "a condition on earlier work or what the starter gave; otherwise this work is skipped" }));
    const blk = s.block && (cat.blocks || {})[s.block];
    if (kind !== "gate" && kind !== "wait" && blocksFor(kind).length) {
      put(el("label", {}, "Use a block",
        el("select", { onchange: (ev) => chooseBlock(s, kind, ev.target.value) },
          el("option", { value: "" }, kind === "task" ? "no: write the task here" : "no: use an action"),
          blocksFor(kind).map((b) => el("option", { value: b.id, selected: s.block === b.id ? "selected" : null },
            `${b.title} (${b.runtime === "prompt" ? "agent prompt" : b.runtime === "python" ? "Python" : "engine"})`))),
        el("small", {}, kind === "task" ? "a prompt block: an agent with the block in its profile carries it out"
          : "a Python or engine block: deterministic, approved and signed")));
    }
    if (blk) put(paramsEditor(s, blk));
    if (kind === "wait") {
      put(el("fieldset", {}, el("legend", {}, "What it waits for"),
        el("small", { class: "muted" }, "Fill in one of the first three."),
        text("Until this holds", s, "until", { placeholder: "e.g. record.status in ['approved']", says: true, help: "checked whenever a record changes, and by the clock" }),
        text("Or for", s, "for", { placeholder: "e.g. 3d", help: "a duration: 30m, 4h, 3d or 1w" }),
        text("Or until", s, "at", { placeholder: "e.g. {{book.appointment}} - 1d", help: "a date or time from the run, moved by + or - a duration if you like" }),
        text("Give up after", s, "timeout", { placeholder: "e.g. 2d", help: "with Until only: then it ends as timed out, which a way out's condition can read" })));
    }
    if (kind === "task" || kind === "gate") {
      const skills = Object.values(cat.skills).map((k) => [k.id, k.title]);
      put(el("fieldset", {}, el("legend", {}, "Who may do it"),
        select("Risk", s, "risk", [["low", "low: an agent's work goes ahead"], ["medium", "medium: goes ahead, the sponsor is told"],
          ["high", "high: held until the sponsor approves"]], {}),
        blk ? null : select("Agent skill", s, "skill", skills, { blank: "none: people only", help: "an agent can take the work only with a skill it has" }),
        kind === "task" ? bool("People only, never an agent", s, "persons_only", { rebuild: false }) : null,
        checks("Not by whoever did", s, "not_by", others.map((o) => [o.id, stepTitle(o)]).concat([["run", "started this work"], ["record.editors", "wrote or changed its record"]]), { none: "no other work" })));
    }
    if (kind === "gate") {
      const outs = outOf(owner.id);
      put(el("fieldset", {}, el("legend", {}, "Decision and signature"),
        el("p", { class: "muted" }, outs.length > 1 ? "Its choices are the ways out of this state: " + outs.map(edgeName).join(", ") + "."
          : "A decision chooses between ways out: give this state more than one."),
        text("Meaning of the signature", s, "meaning", { placeholder: "I have reviewed this and I am responsible for it" }),
        select("Who may decide", s, "signer", [["person", "a person only, who signs"], ["any", "a person, or an agent within criteria (an agent attests, never signs)"]], { rebuild: true }),
        s.signer === "any" ? text("Agents may decide only when", s, "agent_criteria", { placeholder: "e.g. code.amount <= 80", says: true }) : null));
    }
    if (kind === "task" && blk) {
      put(el("fieldset", {}, el("legend", {}, "Instructions"),
        el("small", { class: "muted" }, "The block's prompt is the agent's instructions; a person who takes the work sees it too."),
        select("Review the draft of", s, "review_of", others.map((o) => [o.id, stepTitle(o)]), { blank: "nothing", help: "the person sees that work's answer, prefilled" })));
    } else if (kind === "task") {
      put(el("fieldset", {}, el("legend", {}, "Instructions"),
        text("Shown to whoever does it", s, "instructions", { area: true, rows: 3 }),
        select("Review the draft of", s, "review_of", others.map((o) => [o.id, stepTitle(o)]), { blank: "nothing", help: "the person sees that work's answer, prefilled" })));
      const sk = s.skill && cat.skills[s.skill];
      put(fieldsEditor(s, "fields", "What it produces",
        sk ? `Leave empty to use the skill's fields: ${sk.output.join(", ")}.` : ""));
    }
    if (kind === "task" || kind === "gate") {
      const ctxOpts = [...cat.data_classes.map((d) => ["record:" + d.id, d.name + " records"]), ["input", "what the starter gave"],
        ...cat.providers.map((p) => ["provider:" + p, p]), ...others.map((o) => [o.id, "answers of " + stepTitle(o)])];
      put(el("fieldset", {}, el("legend", {}, "What it reads"),
        checks("Context", s, "context", ctxOpts, { help: "data classes travel with data: whoever takes this work must be allowed to read all of it. To pass one field only, type it below." }),
        csv("Single fields", { get v() { return (s.context || []).filter((c) => !ctxOpts.some(([v]) => v === c)); },
          set v(x) { s.context = (s.context || []).filter((c) => ctxOpts.some(([v]) => v === c)).concat(x); } }, "v",
          { placeholder: "e.g. edit.diagnosis_codes" })));
    } else if (kind === "automation" && !blk) {
      const params = el("textarea", { rows: 8, class: "code", spellcheck: "false",
        oninput: (ev) => { try { s.params = JSON.parse(ev.target.value || "{}"); ev.target.classList.remove("bad-json"); changed(false); }
          catch (e) { ev.target.classList.add("bad-json"); } } });
      params.value = JSON.stringify(s.params || {}, null, 2);
      put(el("fieldset", {}, el("legend", {}, "Action"),
        select("Action", s, "action", cat.actions.map((a) => [a, a]), { blank: "choose" }),
        el("label", {}, "Parameters (JSON)", params,
          el("small", {}, "Values in double braces, e.g. {{run.record}} or {{sort.reason}}, are filled in from the run, as data."))));
    }
    if (type === "do") {
      const earlier = owner.do.slice(0, owner.do.indexOf(s));
      put(el("fieldset", {}, el("legend", {}, "Where the record goes"),
        owner === wf.start ? null : select("Sends the record instead to", s, "to", stateOptions(), { blank: "nowhere else: it goes where the way goes", rebuild: true,
          help: "only when its condition holds: give it Only when as well" }),
        select("If it fails, the record goes to", s, "on_fail", stateOptions(), { blank: "nowhere: it waits, and the practice is told", rebuild: true }),
        checks("Also waits for", s, "after", earlier.map((o) => [o.id, stepTitle(o)]), { none: "no earlier automation on this way",
          help: "it already waits for an earlier one whose answers it reads" })));
    }
    put(removeButton(type === "do" ? "Remove automation" : "Remove work"));
  }
  function deadlineOf() {  // [deadline] only once something is in it
    const get = (k) => (wf.deadline || {})[k];
    const set = (k, v) => { wf.deadline = Object.assign({}, wf.deadline, { [k]: v || undefined });
      if (!wf.deadline.within && !wf.deadline.escalate_to) delete wf.deadline; };
    return { get within() { return get("within"); }, set within(v) { set("within", v); },
      get escalate_to() { return get("escalate_to"); }, set escalate_to(v) { set("escalate_to", v); } };
  }
  function workflowPanel() {
    wf.trigger = wf.trigger || {};
    const roles = cat.roles.map((r) => [r.id, r.name]);
    put(el("h2", {}, "Workflow"),
      text("Title", wf, "title"),
      text("Id", wf, "id", { help: boot.locked_id ? "changing the id makes a new workflow" : "lower-case letters, digits and dashes" }),
      select("About", wf, "record", cat.subject ? [[cat.subject, `a ${cat.subject_label.toLowerCase()}`]] : [], { blank: "nothing in particular" }),
      text("Summary", wf, "summary", { area: true, rows: 3 }),
      el("fieldset", {}, el("legend", {}, "How it starts"),
        checks("Roles that may start it", wf.trigger, "starters", roles),
        text("Or when this event happens", wf.trigger, "event", { placeholder: "e.g. lab.result-received" }),
        text("Or on a schedule", wf.trigger, "schedule", { placeholder: "e.g. weekdays 07:00", help: "hourly, every 15m, daily 07:00, weekdays 07:00, weekly mon,wed,fri 08:00, monthly 1 09:00 or monthly last 17:00" })),
      el("fieldset", {}, el("legend", {}, "Deadline"),
        text("Done within", deadlineOf(), "within", { placeholder: "e.g. 5d", help: "a run still open after this is flagged, once" }),
        select("Then tell", deadlineOf(), "escalate_to", roles, { blank: "the practice's escalation role" })),
      fieldsEditor(wf, "input", "What the starter gives"),
      el("p", { class: "muted" }, "Select a state, its work or a way out on the left to change it, or add a state."));
  }

  // ---- tabs, toolbar, submit
  root.querySelectorAll("[data-add=state]").forEach((b) => b.addEventListener("click", () => { if (wf) addState(); }));
  const tabs = [...root.querySelectorAll(".ed-tabs button[data-tab]")];
  const panels = { visual: root.querySelector("#wf-visual"), toml: root.querySelector("#wf-text") };
  const tabList = root.querySelector(".ed-tabs");
  if (tabList) tabList.setAttribute("role", "tablist");
  tabs.forEach((b) => {
    const p = panels[b.dataset.tab];
    b.setAttribute("role", "tab");
    if (!b.id) b.id = "wf-tab-" + b.dataset.tab;
    if (p) { b.setAttribute("aria-controls", p.id); p.setAttribute("role", "tabpanel"); p.setAttribute("aria-labelledby", b.id); }
  });
  function syncTabs() {
    tabs.forEach((x) => {
      const on = x.dataset.tab === tab;
      x.classList.toggle("on", on);
      x.setAttribute("aria-selected", on ? "true" : "false");
      x.tabIndex = on ? 0 : -1;
    });
    panels.visual.hidden = tab !== "visual";
    panels.toml.hidden = tab !== "toml";
  }
  tabs.forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.tab === tab) return;
    if (b.dataset.tab === "toml") {  // the hub writes the text from the diagram
      const res = await check(true);
      if (!res || typeof res.source !== "string") { status.prepend("The text could not be written. "); return; }
      tomlBox.value = res.source; tab = "toml";
    } else {
      const res = await check(true);
      if (!res || !res.data) { status.prepend("Fix the text before going back to the diagram. "); syncTabs(); return; }
      if (res.old) {
        status.prepend("This is written in the old form, as a list of steps. The diagram edits states and ways out only, "
          + "and does not convert it: keep editing it as text. ");
        syncTabs(); return;
      }
      wf = norm(res.data); sel = null; tab = "visual";
      const old = document.getElementById("wf-old"); if (old) old.hidden = true;
      redraw(); renderPanel();
    }
    syncTabs();
  }));
  if (tabList) tabList.addEventListener("keydown", (ev) => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const n = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1 }[ev.key];
    if (n === undefined) return;
    ev.preventDefault();
    tabs.forEach((x, j) => (x.tabIndex = j === n ? 0 : -1));
    tabs[n].focus();
    tabs[n].click();  // tabs activate when they get focus
  });
  syncTabs();
  tomlBox.addEventListener("input", () => { dirty = true; schedule(); });
  canvas.addEventListener("click", () => { if (sel) choose(null); });
  form.addEventListener("submit", () => {  // the text tab sends its text; the diagram sends its model, which the hub writes out
    if (tab === "toml" || !wf) { sourceIn.value = tomlBox.value; modelIn.value = ""; }
    else { sourceIn.value = ""; modelIn.value = JSON.stringify(wf); }
    dirty = false;
  });
  redraw(); renderPanel();
  if (boot.problems && boot.problems.length) {
    showProblems(boot.problems);
    lastSent = JSON.stringify(tab === "toml" || !wf ? { source: tomlBox.value } : { model: wf });
  } else check();
}

// A form in a dialog over the page, like Create in an issue tracker: a link marked data-modal loads its page and
// shows that page's [data-modal-body] (or its main form) in a <dialog>. Sending stays in the dialog: a refusal is
// shown there with what was typed kept; success goes where the server sends it. Without the script, links are pages.
// A form in parts (a screen with tabs): one part at a time, its tabs on top, Next to move on. Without the script every
// part shows, one under the other, and the tabs are links to them.
function formTabs(root) {
  root.querySelectorAll("nav.form-tabs").forEach((nav) => {
    if (nav.dataset.ready) return;
    nav.dataset.ready = "1";
    const links = [...nav.querySelectorAll("a[href^='#']")];
    const panels = links.map((a) => root.querySelector(a.getAttribute("href")));
    if (panels.some((p) => !p)) return;
    nav.setAttribute("role", "tablist");
    const form = nav.closest("form");
    form?.classList.add("tabbed");
    // the form's own button shows once its last part has been seen (Next leads there); a refused form shows it at once
    const submit = form ? [...form.querySelectorAll("button")].find((b) => b.type === "submit" && !b.closest("fieldset.tabpanel")) : null;
    const refused = !!(form && (form.parentElement || form).querySelector("[role=alert]"));
    const seen = new Set();
    function pick(i, focus) {
      seen.add(i);
      links.forEach((a, j) => {
        const on = i === j;
        a.setAttribute("aria-selected", on ? "true" : "false");
        a.tabIndex = on ? 0 : -1;
        panels[j].hidden = !on;
      });
      if (submit) submit.hidden = !(refused || seen.has(links.length - 1) || seen.size === links.length);
      if (focus) links[i].focus();
    }
    links.forEach((a, i) => {
      a.setAttribute("role", "tab");
      a.removeAttribute("aria-current");
      a.id = a.id || panels[i].id + "-tab";
      a.setAttribute("aria-controls", panels[i].id);
      panels[i].setAttribute("role", "tabpanel");
      panels[i].setAttribute("aria-labelledby", a.id);
      panels[i].tabIndex = -1;
      a.addEventListener("click", (ev) => { ev.preventDefault(); pick(i); });
      if (i < links.length - 1) {
        const next = document.createElement("button");
        next.type = "button";
        next.className = "secondary tab-next";
        next.textContent = "Next: " + links[i + 1].textContent;
        next.addEventListener("click", () => { pick(i + 1); const f = panels[i + 1].querySelector("input:not([type=hidden]), select, textarea"); if (f) f.focus(); });
        panels[i].appendChild(next);
      }
    });
    nav.addEventListener("keydown", (ev) => {
      const i = links.indexOf(document.activeElement);
      if (i < 0) return;
      if (ev.key === " " || ev.key === "Spacebar") { ev.preventDefault(); pick(i, true); return; }
      const n = { ArrowRight: (i + 1) % links.length, ArrowLeft: (i - 1 + links.length) % links.length, Home: 0, End: links.length - 1 }[ev.key];
      if (n === undefined) return;
      ev.preventDefault(); pick(n, true);
    });
    // a required field left empty on a hidden part: show the part of the first such field (once per submit), so the
    // browser can point at it
    let handled = false;
    if (form) form.addEventListener("invalid", (ev) => {
      if (handled) return;
      handled = true;
      setTimeout(() => { handled = false; }, 0);
      const i = panels.findIndex((p) => p.contains(ev.target));
      if (i >= 0 && panels[i].hidden) pick(i);
    }, true);
    // after a refusal: the part holding the field the server marked, or the first field that is not right
    const marked = panels.findIndex((p) => p.querySelector("[aria-invalid=true]"));
    const invalid = refused ? panels.findIndex((p) => [...p.querySelectorAll("input, select, textarea")].some((c) => c.willValidate && !c.checkValidity())) : -1;
    const hashed = panels.findIndex((p) => "#" + p.id === location.hash);
    pick(marked >= 0 ? marked : invalid >= 0 ? invalid : hashed >= 0 ? hashed : 0);
  });
}

// A field only some kinds need (a member's sponsor: agents and services): shown, and asked for, only for those kinds.
// Without the script it is always shown, with its hint.
function slotPickers(root) {  // a day and a person narrow the times offered; the slot is the only value sent
  root.querySelectorAll("[data-slots]").forEach((box) => {
    if (box.dataset.ready) return;
    box.dataset.ready = "1";
    const day = box.querySelector("[data-slot-day]"), who = box.querySelector("[data-slot-with]");
    const time = box.querySelector("[data-slot-time]");
    const sync = () => {
      time.querySelectorAll("optgroup").forEach((g) => {
        let any = false;
        g.querySelectorAll("option").forEach((o) => {
          const on = (!day.value || o.dataset.day === day.value) && (!who.value || o.dataset.with === who.value);
          o.hidden = o.disabled = !on;
          if (on) any = true;
        });
        g.hidden = !any;
      });
      const cur = time.selectedOptions[0];
      if (cur && cur.disabled) time.value = "";
    };
    day.addEventListener("change", sync);
    who.addEventListener("change", sync);
    sync();
  });
}
function kindFields(root) {
  root.querySelectorAll("[data-kinds]").forEach((box) => {
    const form = box.closest("form");
    const kinds_ = form && form.querySelectorAll("[name=kind]");
    const kind = kinds_ && kinds_.length ? { get value() {
      const on = [...kinds_].find((k) => k.type !== "radio" || k.checked); return on ? on.value : ""; } } : null;
    if (!kind || box.dataset.ready) return;
    box.dataset.ready = "1";
    const kinds = box.dataset.kinds.split(/\s+/);
    const ctl = box.querySelector("select, input, textarea");
    const hint = box.querySelector(".kind-hint"), mark = box.querySelector(".req");
    const sync = () => {
      const on = kinds.includes(kind.value);
      box.hidden = !on;
      if (ctl) { ctl.required = on; ctl.disabled = !on; }
      if (hint) hint.hidden = true;
      if (mark) mark.hidden = !on;
    };
    kinds_.forEach((k) => k.addEventListener("change", sync));
    sync();
  });
}

function formModal() {
  let dlg = null, dirty = false, busy = false, downOnBackdrop = false;
  const DISCARD = "Discard what you typed?";
  let stayed = false;  // the dialog shows a reply that stays (advice for the caller): the page reloads after it
  function close() {
    if (dlg) { dlg.close(); dlg.remove(); dlg = null; } dirty = false; back = "";
    if (stayed) { stayed = false; location.reload(); }
  }
  function mayLeave() { return !dirty || confirm(DISCARD); }
  function tryClose() { if (mayLeave()) close(); }
  function frame(title) {
    if (dlg) return;
    dlg = document.createElement("dialog");
    dlg.className = "form-modal";
    dlg.setAttribute("aria-labelledby", "fm-title");
    dlg.innerHTML = '<div class="fm-head"><h2 id="fm-title"></h2><button type="button" class="fm-close" aria-label="Close" title="Close (Esc)">×</button></div><div class="fm-body"></div>';
    dlg.querySelector(".fm-close").addEventListener("click", tryClose);
    dlg.addEventListener("cancel", (ev) => { if (!mayLeave()) ev.preventDefault(); else dirty = false; });
    dlg.addEventListener("close", () => {  // Esc or the backdrop: a reply that stayed still reloads the page
      if (dlg) { dlg.remove(); dlg = null; } dirty = false; back = "";
      if (stayed) { stayed = false; location.reload(); }
    });
    // the backdrop closes it only when the press began there too (not a text selection that ended outside)
    dlg.addEventListener("mousedown", (ev) => { downOnBackdrop = ev.target === dlg; });
    dlg.addEventListener("click", (ev) => { if (ev.target === dlg && downOnBackdrop) tryClose(); downOnBackdrop = false; });
    const body = dlg.querySelector(".fm-body");
    body.addEventListener("input", () => { dirty = true; });
    body.addEventListener("change", () => { dirty = true; });
    document.body.appendChild(dlg);
    dlg.querySelector("#fm-title").textContent = title || "";
    dlg.showModal();
  }
  function show(html, title, refused, clean) {
    frame(title);
    dlg.querySelector("#fm-title").textContent = title;
    const body = dlg.querySelector(".fm-body");
    body.innerHTML = html;
    dirty = !!refused && !clean;  // a refused form still holds what was typed
    body.querySelectorAll("textarea.markdown[id]").forEach(mdEditor);
    formTabs(body);
    kindFields(body);
    slotPickers(body);
    body.querySelectorAll("[data-modal-close]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); close(); }));
    if (back) body.querySelectorAll("form[method=post]").forEach((f) => {  // opened from a board: back to it after
      if (!f.querySelector("input[name=_back]")) f.appendChild(el("input", { type: "hidden", name: "_back", value: back }));
    });
    body.querySelectorAll("form").forEach((f) => f.addEventListener("submit", (ev) => send(ev, f)));
    const alert = refused && body.querySelector("[role=alert]");
    if (alert) { alert.tabIndex = -1; alert.focus(); return; }
    const first = body.querySelector("input:not([type=hidden]):not([disabled]), select, textarea, [contenteditable=true], a[data-modal]");
    if (first) first.focus();
  }
  function extract(text) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    const part = doc.querySelector("main [data-modal-body]") || doc.querySelector("main form") || doc.querySelector("main");
    const h1 = doc.querySelector("main h1");
    return { html: part ? part.innerHTML : "", title: h1 ? h1.textContent.trim() : document.title };
  }
  let back = "";
  async function open(href, words, from) {
    if (busy) return;  // a second click while the first loads does nothing
    busy = true;
    if (!dlg) back = from || "";
    try {
      show('<p role="status">Loading…</p>', dlg ? dlg.querySelector("#fm-title").textContent : words);
      const res = await fetch(href, { credentials: "same-origin", headers: { "X-Modal": "1" } });
      if (res.redirected && !res.url.includes(new URL(href, location.href).pathname)) { location.href = res.url; return; }
      const got = extract(await res.text());
      if (dlg) show(got.html, got.title);
    } catch (e) { location.href = href; }
    finally { busy = false; }
  }
  async function send(ev, f) {
    if (ev.defaultPrevented) return;  // another check (the text editor's required field) stopped it
    if (f.method.toLowerCase() === "get") {  // a step that asks first (which owner?): the next step, in the dialog
      ev.preventDefault();
      const u = new URL(f.action, location.href);
      new FormData(f).forEach((v, k) => u.searchParams.append(k, v));
      dirty = false; open(u.pathname + u.search, ""); return;
    }
    if (f.method.toLowerCase() !== "post" || f.querySelector("input[type=file]")) return;  // files: an ordinary post
    ev.preventDefault();
    f.dataset.posted = "1";  // sent from here: the form stays busy until its answer replaces it
    f.dispatchEvent(new Event("modal-submit"));
    const btn = f.querySelector("button:not([type=button])");
    if (btn) btn.disabled = true;
    try {
      const data = new URLSearchParams(new FormData(f, ev.submitter || undefined));
      const res = await fetch(f.action, { method: "POST", body: data, credentials: "same-origin" });
      if (res.ok && res.redirected) { dirty = false; location.href = res.url; return; }
      const text = await res.text();
      if (res.ok && text.indexOf("data-modal-stay") >= 0) {  // what to tell the caller now: shown, not reloaded away
        const said = extract(text);
        show(said.html, said.title, false, true); stayed = true;
        const advice = dlg && dlg.querySelector(".fm-body [role=alert]");  // read first by a screen reader
        if (advice) { advice.tabIndex = -1; advice.focus(); }
        return;
      }
      if (res.ok) { dirty = false; location.reload(); return; }
      const got = extract(text);
      show(got.html || "<p>That did not work.</p>", dlg ? dlg.querySelector("#fm-title").textContent : got.title, true);
    } catch (e) {
      if (btn) btn.disabled = false;
      freeForm(f);
      f.submit();
    }
  }
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest("a[data-modal]");
    if (!a || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0) return;
    if (!window.HTMLDialogElement) return;
    ev.preventDefault();
    if (busy) return;
    if (dlg && dlg.contains(a) && !mayLeave()) return;  // Back, from a form with something typed
    open(a.getAttribute("href"), a.textContent.trim());
  });
  return { open, show, extract };
}

// A board whose cards move between columns. Each movable card says where it may go (data-moves: a column, and
// whether the move is made at once or asks first on its screen). While a card is dragged, the columns it may go to
// are outlined and the others fade. A drop posts the card's own Move to… form (the same form that works without the
// script, and from the keyboard); the card waits in the new column, marked as moving, until the server accepts the
// move, and the board is then redrawn from the server. A refusal puts the card back and says why in the dialog. A
// move that asks for fields or a comment opens its screen in the dialog instead. Mouse: native drag and drop; touch
// and pen: press and hold, then drag.
function boards(modal) {
  const live = el("div", { class: "visually-hidden", role: "status", "aria-live": "polite" });
  document.body.appendChild(live);
  let drag = null;  // the card being dragged
  const movesOf = (card) => { try { return JSON.parse(card.dataset.moves || "{}"); } catch (e) { return {}; } };
  const colsOf = (card) => [...card.closest(".board").querySelectorAll(".col[data-col]")];
  const colOf = (card, to) => colsOf(card).find((c) => c.dataset.col === to);
  function mark(card, on) {
    const m = movesOf(card);
    colsOf(card).forEach((c) => {
      const home = c.contains(card), ok = !home && Object.prototype.hasOwnProperty.call(m, c.dataset.col);
      c.classList.toggle("drop-ok", on && ok);
      c.classList.toggle("drop-no", on && !ok && !home);
      c.classList.remove("drop-over");
    });
    card.classList.toggle("dragging", on);
  }
  function redraw(text) {  // the boards as the server now has them
    const doc = new DOMParser().parseFromString(text, "text/html");
    const fresh = doc.querySelectorAll("main .board"), mine = document.querySelectorAll("main .board");
    if (fresh.length !== mine.length) return false;
    mine.forEach((b, i) => b.replaceWith(document.adoptNode(fresh[i])));
    return true;
  }
  function count(col, by) { const n = col && col.querySelector("header .n"); if (n) n.textContent = String((parseInt(n.textContent, 10) || 0) + by); }
  async function move(card, to, viaKeys) {
    const m = movesOf(card)[to], form = card.querySelector("form.move-to");
    if (!m || !form) return;
    const here = location.pathname + location.search;
    if (m.how === "screen") { modal.open(m.href, m.label, here); return; }
    const from = card.parentNode, next = card.nextSibling, target = colOf(card, to);
    const cols = colsOf(card), home = cols.find((c) => c.contains(card));
    if (target) { target.appendChild(card); count(target, 1); count(home, -1); }
    card.classList.add("pending"); card.setAttribute("aria-busy", "true");
    live.textContent = "Moving…";
    const data = new URLSearchParams(new FormData(form));
    data.set("to", to);
    let res = null, text = "";
    try {
      res = await fetch(form.action, { method: "POST", body: data, credentials: "same-origin" });
      text = await res.text();
    } catch (e) { res = null; }
    if (res && res.ok) {
      const at = new URL(res.url, location.href);
      if (at.pathname !== location.pathname) { location.href = res.url; return; }
      if (!redraw(text)) { location.reload(); return; }
      live.textContent = "Moved to " + (m.label || to).replace(/…$/, "") + ".";
      const again = document.querySelector('[data-card="' + CSS.escape(card.dataset.card) + '"]');
      if (again && viaKeys) (again.querySelector("select") || again.querySelector("a") || again).focus();
      return;
    }
    // refused, or no answer: back where it was, and why
    if (target) { count(target, -1); count(home, 1); }
    from.insertBefore(card, next);
    card.classList.remove("pending"); card.removeAttribute("aria-busy");
    live.textContent = "Not moved.";
    if (!res) { form.submit(); return; }  // no answer from here: the plain form says what happened
    const got = modal.extract(text);
    modal.show(got.html || "<p>That did not work.</p>", "Not moved", true, true);
    try {  // what changed meanwhile (the reason it was refused) shows on the board behind the dialog
      const now = await fetch(location.href, { credentials: "same-origin" });
      if (now.ok) redraw(await now.text());
    } catch (e) { /* the board stays as it was drawn */ }
  }
  document.addEventListener("submit", (ev) => {  // Move to…, from the keyboard or a click
    const f = ev.target.closest && ev.target.closest("form.move-to");
    const card = f && f.closest("[data-moves]");
    if (!card || !card.closest(".board")) return;
    ev.preventDefault();
    const to = f.elements.to.value;
    if (to) move(card, to, true);
  });
  // ---- mouse: native drag and drop
  document.addEventListener("dragstart", (ev) => {
    const card = ev.target.closest && ev.target.closest(".board [data-moves][draggable=true]");
    if (!card) return;
    drag = card;
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", card.dataset.card);
    requestAnimationFrame(() => mark(card, true));
  });
  document.addEventListener("dragend", () => { if (drag) mark(drag, false); drag = null; });
  const dropCol = (node) => {
    const c = drag && node && node.closest && node.closest(".col.drop-ok");
    return c && c.closest(".board") === drag.closest(".board") ? c : null;
  };
  document.addEventListener("dragover", (ev) => {
    const c = dropCol(ev.target);
    if (!c) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
    colsOf(drag).forEach((x) => x.classList.toggle("drop-over", x === c));
  });
  document.addEventListener("dragleave", (ev) => {
    const c = dropCol(ev.target);
    if (c && !c.contains(ev.relatedTarget)) c.classList.remove("drop-over");
  });
  document.addEventListener("drop", (ev) => {
    const c = dropCol(ev.target);
    if (!c) return;
    ev.preventDefault();
    const card = drag;
    mark(card, false); drag = null;
    move(card, c.dataset.col, false);
  });
  // ---- touch and pen: press and hold, then drag a copy of the card; lifting over a column it may go to moves it
  let press = null;
  function endPress() {
    if (!press) return;
    clearTimeout(press.timer);
    if (press.ghost) press.ghost.remove();
    if (press.on) { mark(press.card, false); document.documentElement.classList.remove("touch-dragging"); }
    press = null;
  }
  const under = (x, y) => {
    const n = document.elementFromPoint(x, y);
    const c = n && n.closest(".col.drop-ok");
    return c && press && c.closest(".board") === press.card.closest(".board") ? c : null;
  };
  document.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType === "mouse" || press) return;
    const card = ev.target.closest && ev.target.closest(".board [data-moves]");
    if (!card || ev.target.closest("form.move-to")) return;
    press = { card, x: ev.clientX, y: ev.clientY, on: false, ghost: null, id: ev.pointerId };
    press.timer = setTimeout(() => {
      if (!press) return;
      press.on = true;
      const r = card.getBoundingClientRect();
      press.dx = press.x - r.left; press.dy = press.y - r.top;
      press.ghost = card.cloneNode(true);
      press.ghost.classList.add("drag-ghost");
      press.ghost.removeAttribute("data-card");
      press.ghost.setAttribute("aria-hidden", "true");
      Object.assign(press.ghost.style, { width: r.width + "px", left: r.left + "px", top: r.top + "px" });
      document.body.appendChild(press.ghost);
      document.documentElement.classList.add("touch-dragging");
      mark(card, true);
      if (navigator.vibrate) navigator.vibrate(10);
    }, 350);
  });
  document.addEventListener("pointermove", (ev) => {
    if (!press || ev.pointerId !== press.id) return;
    if (!press.on) {  // moving before the hold: a scroll, not a drag
      if (Math.abs(ev.clientX - press.x) > 8 || Math.abs(ev.clientY - press.y) > 8) endPress();
      return;
    }
    press.ghost.style.left = ev.clientX - press.dx + "px";
    press.ghost.style.top = ev.clientY - press.dy + "px";
    const c = under(ev.clientX, ev.clientY);
    colsOf(press.card).forEach((x) => x.classList.toggle("drop-over", x === c));
  });
  document.addEventListener("touchmove", (ev) => { if (press && press.on) ev.preventDefault(); }, { passive: false });
  document.addEventListener("pointerup", (ev) => {
    if (!press || ev.pointerId !== press.id) return;
    const was = press.on, card = press.card, c = was ? under(ev.clientX, ev.clientY) : null;
    endPress();
    if (was) {
      const stop = (e) => { e.preventDefault(); e.stopPropagation(); };  // the lift is not also a click on the card
      document.addEventListener("click", stop, { capture: true, once: true });
      setTimeout(() => document.removeEventListener("click", stop, { capture: true }), 400);
    }
    if (c) move(card, c.dataset.col, false);
  });
  document.addEventListener("pointercancel", endPress);
  document.addEventListener("contextmenu", (ev) => { if (press && press.on) ev.preventDefault(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && press) endPress(); });
}

// What a record's latest move set going (RecordWork._activity_html), kept current while some of it is still open:
// the block alone is fetched again (every 4 s, every 15 s after two minutes, only while the tab is shown) and swapped
// when its ETag changes, saying what changed in a polite live region. It stops when the block comes back without
// data-live. When the record itself has moved on (data-at differs), the page's forms are left as they are and a
// status line offers the page as it is now, so nothing typed is lost. Without the script: the block's Refresh link.
function activity() {
  let sec = document.querySelector("#activity[data-live]");
  if (!sec) return;
  const at0 = sec.dataset.at, t0 = Date.now();
  const say = el("div", { class: "visually-hidden", role: "status", "aria-live": "polite" });
  document.body.appendChild(say);
  let tag = null, paused = false, told = false;
  const words = (n) => (n.textContent || "").replace(/\s+/g, " ").trim();
  const lines = (n) => [...n.querySelectorAll("li")].map(words);
  const later = () => setTimeout(poll, Date.now() - t0 > 120000 ? 15000 : 4000);
  async function poll() {
    if (!sec || !sec.dataset.live) return;
    if (document.hidden) { paused = true; return; }
    let res = null;
    try {
      res = await fetch(sec.dataset.live, { credentials: "same-origin", headers: tag ? { "If-None-Match": tag } : {} });
    } catch (e) { res = null; }
    if (!res || res.status === 304) { later(); return; }
    if (!res.ok) { if (res.status >= 500) later(); return; }  // refused or gone: stop asking
    tag = res.headers.get("ETag");
    const fresh = new DOMParser().parseFromString(await res.text(), "text/html").getElementById("activity");
    if (!fresh) return;
    const before = new Set(lines(sec)), was = sec.querySelector("details");
    const now = fresh.querySelector("details");
    if (was && now) now.open = was.open || !!fresh.dataset.live;  // what the reader opened stays open
    const node = document.adoptNode(fresh);
    sec.replaceWith(node);
    sec = node;
    const changed = lines(sec).filter((t) => !before.has(t));
    if (changed.length) say.textContent = "Updated: " + changed.slice(0, 3).join("; ");
    else if (!sec.dataset.live) say.textContent = "Everything this move set going has settled.";
    if (!told && sec.dataset.at !== undefined && at0 !== undefined && sec.dataset.at !== at0) {
      told = true;
      const work = document.getElementById("work");
      const note = el("p", { class: "flash info moved-on", role: "status" },
        "This has moved on since you opened it. ", el("a", { href: location.pathname + location.search }, "Show it as it is now"));
      if (work) work.prepend(note);
    }
    if (sec.dataset.live) later();
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && paused) { paused = false; poll(); }
  });
  later();
}

window.PH = { MD, el };  // for the live editor's script, which follows
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".avatar-menu").forEach(avatarMenu);
  const modal = formModal();
  if (document.querySelector(".board [data-moves]")) boards(modal);
  activity();
  document.querySelectorAll("textarea.markdown[id]").forEach(mdEditor);
  formTabs(document);
  kindFields(document);
  slotPickers(document);
  document.querySelectorAll("svg[data-flow]").forEach((svg) => {
    const src = document.getElementById(svg.dataset.flow);
    if (src) drawFlow(svg, JSON.parse(src.textContent));
  });
  const ed = document.getElementById("wf-editor");
  if (ed) editor(ed);
});
})();
"""

# ---------------------------------------------------------------------------------------------- writing together
# The live editor: a page's text edited by several people at once (see practicehub/live.py). Its operations are those
# of practicehub/ot.py, part for part; lengths are UTF-16 units, as JavaScript strings count them. Nothing typed is
# ever evaluated: the text is markdown, shown through MD.toHtml, which escapes everything it does not know.
LIVE_JS = r"""
(function () {
"use strict";
const { MD, el } = window.PH || {};
if (!MD) return;

// ---- operations (practicehub/ot.py)
const OT = (() => {
  const isIns = (p) => typeof p === "string", isRet = (p) => typeof p === "number" && p > 0;
  function normalize(op) {
    const out = [];
    for (const p of op) {
      if (p === 0 || p === "") continue;
      const last = out[out.length - 1];
      if (isIns(p)) {
        if (typeof last === "number" && last < 0) {
          if (out.length >= 2 && isIns(out[out.length - 2])) out[out.length - 2] += p;
          else out.splice(out.length - 1, 0, p);
        } else if (isIns(last)) out[out.length - 1] += p;
        else out.push(p);
      } else if (typeof last === "number" && (last > 0) === (p > 0)) out[out.length - 1] += p;
      else out.push(p);
    }
    return out;
  }
  const baseLen = (op) => op.reduce((n, p) => n + (typeof p === "number" ? Math.abs(p) : 0), 0);
  const targetLen = (op) => op.reduce((n, p) => n + (isIns(p) ? p.length : p > 0 ? p : 0), 0);
  function apply(op, text) {
    if (baseLen(op) !== text.length) throw new Error("operation does not fit the text");
    let out = "", pos = 0;
    for (const p of op) {
      if (isIns(p)) out += p;
      else if (p > 0) { out += text.slice(pos, pos + p); pos += p; }
      else pos -= p;
    }
    return out;
  }
  const plen = (p) => (isIns(p) ? p.length : Math.abs(p));
  function split(p, n) {
    if (isIns(p)) return [p.slice(0, n), p.slice(n) || null];
    if (p > 0) return [n, p - n || null];
    return [-n, p + n || null];
  }
  function walker(a) { let i = 0; return { cur: a[0] === undefined ? null : a[0], next() { i++; this.cur = i < a.length ? a[i] : null; } }; }
  function transform(a, b) {
    a = normalize(a); b = normalize(b);
    if (baseLen(a) !== baseLen(b)) throw new Error("not made on the same text");
    const oa = [], ob = [], A = walker(a), B = walker(b);
    while (A.cur !== null || B.cur !== null) {
      if (A.cur !== null && isIns(A.cur)) { oa.push(A.cur); ob.push(A.cur.length); A.next(); continue; }
      if (B.cur !== null && isIns(B.cur)) { oa.push(B.cur.length); ob.push(B.cur); B.next(); continue; }
      if (A.cur === null || B.cur === null) throw new Error("not made on the same text");
      const n = Math.min(Math.abs(A.cur), Math.abs(B.cur));
      const [xh, xr] = split(A.cur, n), [yh, yr] = split(B.cur, n);
      if (xh > 0 && yh > 0) { oa.push(n); ob.push(n); }
      else if (xh < 0 && yh > 0) oa.push(xh);
      else if (xh > 0 && yh < 0) ob.push(yh);
      if (xr !== null) A.cur = xr; else A.next();
      if (yr !== null) B.cur = yr; else B.next();
    }
    return [normalize(oa), normalize(ob)];
  }
  function compose(a, b) {
    a = normalize(a); b = normalize(b);
    if (targetLen(a) !== baseLen(b)) throw new Error("does not follow");
    const out = [], A = walker(a), B = walker(b);
    while (A.cur !== null || B.cur !== null) {
      if (A.cur !== null && typeof A.cur === "number" && A.cur < 0) { out.push(A.cur); A.next(); continue; }
      if (B.cur !== null && isIns(B.cur)) { out.push(B.cur); B.next(); continue; }
      if (A.cur === null || B.cur === null) throw new Error("does not follow");
      const n = Math.min(plen(A.cur), Math.abs(B.cur));
      const [xh, xr] = split(A.cur, n), [yh, yr] = split(B.cur, n);
      if (yh > 0) out.push(xh);
      else if (typeof xh === "number") out.push(yh);
      if (xr !== null) A.cur = xr; else A.next();
      if (yr !== null) B.cur = yr; else B.next();
    }
    return normalize(out);
  }
  function diff(old, now, caret) {
    let p = 0, s = 0;
    const lim = Math.min(old.length, now.length);
    // the change is where the caret is: the common start stops there
    const pmax = typeof caret === "number" ? Math.min(lim, Math.max(0, caret - Math.max(0, now.length - old.length))) : lim;
    while (p < pmax && old[p] === now[p]) p++;
    while (s < old.length - p && s < now.length - p && old[old.length - 1 - s] === now[now.length - 1 - s]) s++;
    const hi = (c) => c >= 0xD800 && c <= 0xDBFF, lo = (c) => c >= 0xDC00 && c <= 0xDFFF;
    while (p && hi(old.charCodeAt(p - 1))) p--;
    while (s && lo(old.charCodeAt(old.length - s))) s--;
    return normalize([p, now.slice(p, now.length - s), -(old.length - p - s), s]);
  }
  function transformIndex(op, index, beforeInsert) {
    let pos = 0, out = index;
    for (const p of normalize(op)) {
      if (pos > index || (pos === index && beforeInsert && isIns(p))) break;
      if (isIns(p)) out += p.length;
      else if (p > 0) pos += p;
      else { out -= Math.min(-p, index - pos); pos -= p; }
    }
    return Math.max(out, 0);
  }
  const noop = (op) => normalize(op).every(isRet);
  return { normalize, apply, transform, compose, diff, transformIndex, noop };
})();
window.PH.OT = OT;

// ---- talking to the server
async function call(method, url, body) {
  const r = await fetch(url, { method, credentials: "same-origin", headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (_) { /* not JSON */ }
  if (!r.ok) { const e = new Error(data.error || ("HTTP " + r.status)); e.status = r.status; throw e; }
  return data;
}
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const initials = (n) => n.split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();

// ---- blocks: a page is re-rendered one block at a time, so the rest stays as it was (and selection with it)
function blocks(text, titles) {
  const t = document.createElement("template");
  t.innerHTML = MD.toHtml(text, titles);
  return [...t.content.childNodes];
}
function patch(host, oldText, newText, titles, prev) {
  const before = prev || blocks(oldText, titles).map((n) => n.outerHTML || n.textContent);
  const nodes = blocks(newText, titles), after = nodes.map((n) => n.outerHTML || n.textContent);
  const kids = [...host.children];
  if (kids.length !== before.length) { host.replaceChildren(...nodes); return after; }
  let a = 0;
  while (a < before.length && a < after.length && before[a] === after[a]) a++;
  let z = 0;
  while (z < before.length - a && z < after.length - a && before[before.length - 1 - z] === after[after.length - 1 - z]) z++;
  const anchor = kids[before.length - z] || null;
  for (let i = a; i < before.length - z; i++) kids[i].remove();
  for (let i = a; i < after.length - z; i++) host.insertBefore(nodes[i], anchor);
  return after;
}

// ---- the people in it
function faces(members, cfg, me) {
  return el("span", { class: "live-faces" }, ...members.map((m) => el("span", {
    class: "av" + (m.id === me ? " me" : ""), style: `background:${cfg.colors[m.color % cfg.colors.length]}`,
    title: m.name + (m.id === me ? " (you)" : "") }, initials(m.name))));
}

// ---- a caret's place in a text box: a copy of the box, its text up to the caret, and a marker
function caretBox(area, mirror, index) {
  const cs = getComputedStyle(area);
  for (const k of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingRight",
    "paddingBottom", "paddingLeft", "borderTopWidth", "borderLeftWidth", "boxSizing", "tabSize"]) mirror.style[k] = cs[k];
  mirror.style.width = area.clientWidth + "px";
  mirror.textContent = area.value.slice(0, index);
  const mark = el("span", {}, "​");
  mirror.append(mark);
  const r = { top: mark.offsetTop - area.scrollTop, left: mark.offsetLeft - area.scrollLeft, h: mark.offsetHeight };
  mirror.textContent = "";
  return r;
}

// ---- what can be put in a page: the toolbar's Insert and "/" at the start of a line. ``md``: what the markdown view
// writes; ``act``: what the visual view does; ``back``: how far before the end of ``md`` the caret goes.
const SLASH = [
  { label: "Heading", keys: "heading title h2", md: "## ", act: "h2" },
  { label: "Subheading", keys: "subheading h3", md: "### ", act: "h3" },
  { label: "Bulleted list", keys: "list bullet", md: "- ", act: "ul" },
  { label: "Numbered list", keys: "list number ordered", md: "1. ", act: "ol" },
  { label: "Checklist", keys: "check task todo action", md: "- [ ] ", act: "task" },
  { label: "Table", keys: "table grid", md: "| Column | Column |\n| --- | --- |\n|  |  |", act: "table" },
  { label: "Code block", keys: "code pre snippet", md: "```\n\n```", back: 4, act: "pre" },
  { label: "Info panel", keys: "panel callout note info", md: "> [!note]\n> ", act: "note" },
  { label: "Tip panel", keys: "panel callout tip success", md: "> [!tip]\n> ", act: "tip" },
  { label: "Warning panel", keys: "panel callout warning caution", md: "> [!warning]\n> ", act: "warning" },
  { label: "Quote", keys: "quote blockquote", md: "> ", act: "quote" },
  { label: "Mention a person or agent", keys: "mention @ person agent", md: "@", act: "at" },
  { label: "Link to a page", keys: "link page record wiki", md: "[[]]", back: 2, act: "page" },
  { label: "Link", keys: "link address url web", md: "[words](https://)", back: 1, act: "link" },
  { label: "Divider", keys: "divider rule line", md: "---\n", act: "hr" },
];
const ICON = {  // 24 x 24, drawn with a stroke (as the rest of the hub's icons)
  ul: '<circle cx="5" cy="7" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="5" cy="17" r="1.2"/><path d="M9 7h11M9 12h11M9 17h11"/>',
  ol: '<path d="M4 5h1.5v4M4 9h3M4 14.5c0-1 2.8-1 2.8.3 0 1-2.8 1.8-2.8 3.2h3M10 7h10M10 12h10M10 17h10"/>',
  task: '<rect x="3.5" y="4.5" width="6" height="6" rx="1"/><path d="M4.8 7.6l1.4 1.4 2.4-2.6M3.5 14.5h6v6h-6zM13 7.5h8M13 17.5h8"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  page: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h4"/>',
  at: '<circle cx="12" cy="12" r="3.5"/><path d="M15.5 12v1.5a2.5 2.5 0 0 0 5 0V12a8.5 8.5 0 1 0-3.4 6.8"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 9.5h18M3 15h18M9 4v16M15 4v16"/>',
  code: '<path d="M8.5 7L3.5 12l5 5M15.5 7l5 5-5 5"/>',
  pre: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 10l-2 2 2 2M12 14h5"/>',
  note: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.6v.4"/>',
  hr: '<path d="M3 12h18M7 7h10M7 17h10" stroke-dasharray="0"/>',
  slash: '<path d="M15 4L9 20"/>',
};
const icon = (k) => { const s = el("span", { class: "live-ic", "aria-hidden": "true" }); s.innerHTML =
  `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[k]}</svg>`; return s; };
const KINDS = ["note", "tip", "warning", "important"], KIND_WORD = { note: "Note", tip: "Tip", warning: "Warning", important: "Important" };

// ---- where a caret in the markdown of one block is among the words shown for it, and back. The marks (## , - ,
// **, [[, ](address) and so on) are not shown; everything else is, one for one. Close enough to place someone's
// caret, or to keep yours where it was when someone else's change redraws the paragraph you are in.
function hiddenOf(src) {
  const h = new Uint8Array(src.length + 1);
  const hide = (a, b) => { for (let k = a; k < b && k < src.length; k++) h[k] = 1; };
  const lines = src.split("\n");
  let at = 0, fenced = false;
  lines.forEach((line, n) => {
    const end = at + line.length, isFence = /^\s*```/.test(line);
    if (isFence) { hide(at, end); fenced = !fenced; }
    else if (fenced) { /* code: shown as written */ }
    else if (/^[\s|:-]+$/.test(line) && /-{2,}/.test(line) && /^\s*\|/.test(line)) hide(at, end);  // a table's rule
    else if (/^\s*(>\s?)*\s*\[!\w+\]\s*$/.test(line) || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) hide(at, end);
    else {
      const m = /^\s*(?:>\s?)*\s*(?:#{1,4}\s+|(?:[-*+]|\d{1,3}\.)\s+(?:\[[ xX]\]\s+)?)?/.exec(line);
      hide(at, at + m[0].length);
      if (/^\s*\|/.test(line)) for (let k = 0; k < line.length; k++) if (line[k] === "|") h[at + k] = 1;
      const re = /\[\[[^\]|\n]*\||\[\[|\]\]|\]\([^)\s]*\)|\*\*|~~|`|\*|\[/g;
      let m2;
      while ((m2 = re.exec(line))) hide(at + m2.index, at + m2.index + m2[0].length);
    }
    // a line break shows inside code (not before its closing fence); elsewhere it is a new line or a new item
    const next = lines[n + 1];
    if (n < lines.length - 1 && !(fenced && !isFence && next !== undefined && !/^\s*```/.test(next))) h[end] = 1;
    at = end + 1;
  });
  return h;
}
function shownBefore(src, k) {
  const h = hiddenOf(src);
  let v = 0;
  for (let i = 0; i < k && i < src.length; i++) if (!h[i]) v++;
  return v;
}
function indexOfShown(src, vis) {
  const h = hiddenOf(src);
  let v = 0;
  for (let i = 0; i <= src.length; i++) {
    if (v === vis && (i === src.length || !h[i])) return i;
    if (i < src.length && !h[i]) v++;
  }
  return src.length;
}

function liveEditor(article, cfg) {
  const base = `/api/live/${cfg.ref}/${encodeURIComponent(cfg.field)}`;
  const titles = {};
  article.querySelectorAll("[data-mention]").forEach((m) => {
    const src = m.dataset.mention; if (!src.includes("|")) titles[src] = m.textContent.replace(/\s*\(no page or record found\)\s*$/, "");
  });
  const say = el("div", { class: "visually-hidden", "aria-live": "polite", role: "status" });
  document.body.append(say);
  const announce = (t) => { say.textContent = ""; setTimeout(() => { say.textContent = t; }, 30); };
  let state = null, text = "", seq = 0, pv = -1, editing = false, stopped = false, rendered = null;
  let inflight = null, inflightCid = null, buffer = null, sending = false, cidN = 0, lastMembers = null;
  const sessionId = Math.random().toString(36).slice(2, 10);
  const newCid = () => `${cfg.me.replace(/[^A-Za-z0-9_.:-]/g, "")}-${sessionId}-${++cidN}`.slice(0, 80);
  const nowBar = document.querySelector("[data-live-now]");
  let mode = "visual";  // "visual": the page as it reads, edited in place; "code": its markdown
  try { if (localStorage.getItem("ph-live-mode") === "code") mode = "code"; } catch (_) { /* no storage */ }

  // the editor, made when Edit is pressed: over the whole window, as the page will read
  let wrap, area, mirror, carets, vcarets, status, faceHost, menu, surface, surfWrap, codeWrap, canvas, sheet;
  let titleIn = null, styleSel, modeVis, modeCode, ask, askIn, askLabel, askHint;
  function setStatus(t, kind) { if (status) { status.textContent = t; status.className = "live-status " + (kind || ""); } }

  function build() {
    area = el("textarea", { class: "live-text", "aria-label": "Page text, in markdown", spellcheck: "true",
      "aria-describedby": "live-help" });
    mirror = el("div", { class: "live-mirror", "aria-hidden": "true" });
    carets = el("div", { class: "live-carets", "aria-hidden": "true" });
    vcarets = el("div", { class: "live-carets", "aria-hidden": "true" });
    surface = el("div", { class: "prose live-surface", contenteditable: "true", role: "textbox", "aria-multiline": "true",
      "aria-label": "Page text", spellcheck: "true", "aria-describedby": "live-help" });
    status = el("span", { class: "live-status", role: "status" });
    faceHost = el("span", { class: "live-people" });
    const B = (label, key, face, fn, extra) => el("button", { type: "button", class: "md-btn", title: label + (key ? ` (${key})` : ""),
      "aria-label": label, onmousedown: (ev) => ev.preventDefault(), onclick: (ev) => { ev.preventDefault(); fn(); }, ...(extra || {}) }, face);
    styleSel = el("select", { class: "md-style", "aria-label": "Text style", onchange: () => { blockStyle(styleSel.value); } },
      el("option", { value: "p" }, "Normal text"), el("option", { value: "h2" }, "Heading"),
      el("option", { value: "h3" }, "Subheading"), el("option", { value: "h4" }, "Small heading"),
      el("option", { value: "quote" }, "Quote"), el("option", { value: "pre" }, "Code block"));
    const sep = () => el("span", { class: "md-sep", "aria-hidden": "true" });
    const bar = el("div", { class: "live-toolbar", role: "toolbar", "aria-label": "Formatting" },
      styleSel, sep(),
      B("Bold", "Ctrl+B", el("b", {}, "B"), () => inlineMark("**"), { "data-mark": "bold" }),
      B("Italic", "Ctrl+I", el("i", {}, "I"), () => inlineMark("*"), { "data-mark": "italic" }),
      B("Strikethrough", "", el("s", {}, "S"), () => inlineMark("~~"), { "data-mark": "strikeThrough" }),
      B("Code", "", icon("code"), () => inlineMark("`")), sep(),
      B("Bulleted list", "", icon("ul"), () => insert("ul")), B("Numbered list", "", icon("ol"), () => insert("ol")),
      B("Checklist", "[] ", icon("task"), () => insert("task")), sep(),
      B("Link", "Ctrl+K", icon("link"), () => insert("link")), B("Link to a page", "[[", icon("page"), () => insert("page")),
      B("Mention a person or agent", "@", icon("at"), () => insert("at")), sep(),
      B("Table", "", icon("table"), () => insert("table")), B("Code block", "```", icon("pre"), () => insert("pre")),
      B("Info panel", "", icon("note"), () => insert("note")), B("Divider", "---", icon("hr"), () => insert("hr")),
      B("Insert…", "/", el("span", { class: "live-ins" }, icon("slash"), el("span", { class: "live-ins-w" }, "Insert")), () => openMenu(true)));
    modeVis = el("button", { type: "button", class: "md-mode", "aria-pressed": "true", onclick: () => setMode("visual", true) }, "Visual");
    modeCode = el("button", { type: "button", class: "md-mode", "aria-pressed": "false", onclick: () => setMode("code", true),
      title: "Edit the markdown (Ctrl+Shift+M)" }, "Markdown");
    const where = document.querySelector("main h1");
    const update = cfg.publish
      ? el("button", { type: "button", class: "primary", onclick: () => finish(cfg.publish), title: "Save a version and " + cfg.publish.label.toLowerCase() + " it" }, cfg.publish.label)
      : el("button", { type: "button", class: "primary", onclick: () => finish(), title: "Save a version now and stop editing" }, "Update");
    const head = el("div", { class: "live-head" },
      el("span", { class: "live-where" }, el("span", { class: "lozenge editing-flag" }, "Editing"), " ",
        el("span", { class: "live-where-t" }, where ? where.textContent : "")),
      faceHost, status,
      el("span", { class: "seg live-modes", role: "group", "aria-label": "View" }, modeVis, modeCode),
      el("button", { type: "button", class: "secondary", onclick: () => close(), title: "Stop editing: what you wrote is kept, and saved as a version when everyone has left" }, "Close"),
      update);
    askLabel = el("span", { class: "md-ask-label" });
    askIn = el("input", { type: "text", class: "md-ask-in" });
    askHint = el("span", { class: "md-ask-hint", role: "status" });
    ask = el("div", { class: "md-ask live-ask", hidden: true }, askLabel, askIn,
      el("button", { type: "button", class: "btn primary", onclick: () => finishAsk() }, "Add"),
      el("button", { type: "button", class: "btn subtle", onclick: () => closeAsk() }, "Cancel"), askHint);
    askIn.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); finishAsk(); }
      if (ev.key === "Escape") { ev.preventDefault(); closeAsk(); }
    });
    if (cfg.title !== undefined && cfg.title !== null) {
      titleIn = el("input", { type: "text", class: "live-title", "aria-label": "Title", maxlength: "200", value: cfg.title,
        placeholder: "Give this page a title" });
      titleIn.addEventListener("input", () => { clearTimeout(saveTitle.t); saveTitle.t = setTimeout(saveTitle, 700); });
      titleIn.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); saveTitle(); (mode === "visual" ? surface : area).focus(); }
      });
    }
    surfWrap = el("div", { class: "live-surf" }, surface, vcarets);
    codeWrap = el("div", { class: "live-code" }, area, carets);
    const help = el("p", { id: "live-help", class: "muted live-help" },
      "Saved as you type, for everyone in the page; a version is kept when you press ", cfg.publish ? cfg.publish.label : "Update",
      " or stop for two minutes. Type / for headings, lists, tables and more, and @ to mention a person or an agent.");
    sheet = el("div", { class: "live-page" }, titleIn || el("h1", { class: "live-title" }, where ? where.textContent : ""),
      surfWrap, codeWrap, help);
    canvas = el("div", { class: "live-canvas" }, sheet, mirror);
    wrap = el("div", { class: "live-editor", role: "region", "aria-label": "Editing the page with others" }, head, bar, ask, canvas);
    document.body.append(wrap);
    document.body.classList.add("live-editing");
    // markdown view
    atPicker(area, cfg.ref, (m) => {  // registered first: while its list is open, it takes the arrow keys
      const r = caretBox(area, mirror, area.selectionStart);
      m.style.top = (area.offsetTop + r.top + r.h + 4) + "px";
      m.style.left = (area.offsetLeft + Math.max(0, Math.min(r.left, area.clientWidth - 280))) + "px";
    });
    area.addEventListener("input", onInput);
    area.addEventListener("keydown", onKey);
    area.addEventListener("paste", onPaste);
    area.addEventListener("scroll", drawCarets);
    for (const ev of ["keyup", "mouseup", "select"]) area.addEventListener(ev, sendCaretSoon);
    // visual view
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (_) { /* older browsers */ }
    atPickerCE(surface, cfg.ref);
    surface.addEventListener("compositionstart", () => { composing = true; });
    surface.addEventListener("compositionend", () => { composing = false; onVInput(null); });
    surface.addEventListener("input", onVInput);
    surface.addEventListener("keydown", onVKey);
    surface.addEventListener("paste", (ev) => { if (!ev.clipboardData) return; ev.preventDefault(); insertFrom(ev.clipboardData); });
    surface.addEventListener("drop", (ev) => {
      ev.preventDefault();
      if (!ev.dataTransfer) return;
      const at = document.caretRangeFromPoint ? document.caretRangeFromPoint(ev.clientX, ev.clientY) : null;
      if (at) { const s = getSelection(); s.removeAllRanges(); s.addRange(at); }
      insertFrom(ev.dataTransfer);
    });
    surface.addEventListener("click", onVClick);
    document.addEventListener("selectionchange", () => {
      if (!editing || mode !== "visual" || !surface.contains(getSelection().anchorNode)) return;
      refreshBar(); sendCaretSoon();
    });
    wrap.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && ev.key.toLowerCase() === "m") {
        ev.preventDefault(); setMode(mode === "visual" ? "code" : "visual", true);
      }
    });
    canvas.addEventListener("scroll", () => { if (menuAt >= 0 || vMenu) renderMenu(); });
    window.addEventListener("resize", drawCarets);
  }

  function setMode(m, focus) {
    if (mode === "visual" && surface && !surfWrap.hidden) syncSurface();
    mode = m;
    try { localStorage.setItem("ph-live-mode", m); } catch (_) { /* no storage */ }
    closeMenu(); closeAsk(true);
    surfWrap.hidden = m !== "visual"; codeWrap.hidden = m !== "code";
    sheet.classList.toggle("code", m === "code");
    modeVis.setAttribute("aria-pressed", String(m === "visual"));
    modeCode.setAttribute("aria-pressed", String(m === "code"));
    if (m === "visual") { renderSurface(text); if (focus) { surface.focus(); setCaretMd([0, 0]); } }
    else { area.value = text; if (focus) { area.focus(); area.setSelectionRange(0, 0); } }
    drawCarets();
  }

  // ---- local changes
  function local(op) {
    if (OT.noop(op)) return;
    buffer = buffer ? OT.compose(buffer, op) : op;
    text = OT.apply(op, text);
    flush();
  }
  function onInput() {
    if (area.value === text) return;
    const op = OT.diff(text, area.value, area.selectionEnd);
    local(op);
    if (menuAt >= 0) renderMenu();
    shortcut();
  }
  function replace(from, to, words, caretAt) {
    area.setRangeText(words, from, to, "end");
    if (caretAt !== undefined) area.setSelectionRange(caretAt, caretAt);
    onInput();
  }
  function wrapSel(mark) {
    const a = area.selectionStart, b = area.selectionEnd, sel = area.value.slice(a, b);
    replace(a, b, mark + (sel || "words") + mark);
    area.setSelectionRange(a + mark.length, a + mark.length + (sel || "words").length);
  }
  function lineStart(at) { return area.value.lastIndexOf("\n", at - 1) + 1; }
  function linePrefix(p) {
    const s = lineStart(area.selectionStart);
    const line = area.value.slice(s).split("\n")[0];
    const cur = /^(#{1,4} |- \[[ xX]\] |[-*+] |\d+\. |> )/.exec(line);
    replace(s, s + (cur ? cur[0].length : 0), cur && cur[0] === p ? "" : p);
  }
  function link() {
    const a = area.selectionStart, b = area.selectionEnd, sel = area.value.slice(a, b) || "words";
    replace(a, b, `[${sel}](https://)`);
    area.setSelectionRange(a + sel.length + 3, a + sel.length + 11);
  }
  // markdown as it is typed: "[] " is a checklist item, ``` closes itself, Enter carries a list on
  function shortcut() {
    const at = area.selectionStart, s = lineStart(at), line = area.value.slice(s, at);
    if (line === "[] " || line === "[ ] ") replace(s, at, "- [ ] ");
    else if (line === "```" && area.value.slice(at, at + 1) !== "`" && area.value.slice(at).split("\n")[0] === "")
      replace(at, at, "\n\n```", at + 1);
    else if (line === "/" ) openMenu(false);
  }
  function onKey(ev) {
    if (menu && !menu.hidden && menuKey(ev)) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (mod && ev.key.toLowerCase() === "b") { ev.preventDefault(); wrapSel("**"); }
    else if (mod && ev.key.toLowerCase() === "i") { ev.preventDefault(); wrapSel("*"); }
    else if (mod && ev.key.toLowerCase() === "k") { ev.preventDefault(); link(); }
    else if (ev.key === "Enter" && !ev.shiftKey && !mod && area.selectionStart === area.selectionEnd) {
      const at = area.selectionStart, s = lineStart(at), line = area.value.slice(s, at);
      const m = /^(\s*)(- \[[ xX]\] |[-*+] |(\d+)\. )(.*)$/.exec(line);
      if (!m) return;
      ev.preventDefault();
      if (!m[4].trim()) { replace(s, at, ""); return; }  // Enter on an empty item ends the list
      const next = m[3] ? `${+m[3] + 1}. ` : m[2].startsWith("- [") ? "- [ ] " : m[2];
      replace(at, at, "\n" + m[1] + next);
    }
  }
  function onPaste(ev) {
    const html = ev.clipboardData && ev.clipboardData.getData("text/html");
    if (!html) return;  // plain text goes in as it is
    ev.preventDefault();
    const md = MD.toMd(new DOMParser().parseFromString(html, "text/html").body);
    replace(area.selectionStart, area.selectionEnd, md);
  }

  // ---- the visual view. Each top-level element of the surface was drawn from one block of the markdown and keeps
  // it (``meta``: its markdown, the space before it, where it starts, and how the element looked when drawn). After
  // each change the surface is written back as markdown: an element that is as it was gives back its own markdown,
  // character for character, and only a changed one is written anew (MD.toMd). The difference from the text is
  // one operation, sent as the markdown view's are: so typing in either view is the same stream of text operations,
  // and someone else's change redraws only the blocks it touched.
  const meta = new WeakMap();
  let lead = "", tail = "", composing = false;
  const blank = (g) => /\n[ \t\r]*\n/.test(g);
  const nodeOf = (html) => { const t = document.createElement("template"); t.innerHTML = html; return t.content.firstElementChild; };
  const tagOf = (html) => ((/^<(\w+)/.exec(html) || [])[1] || "").toUpperCase();
  const mdOf = (nodes) => {
    const box = document.createElement("div");
    nodes.forEach((n) => box.append(n.cloneNode(true)));
    const w = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);  // the editor's place-holding spaces are not text
    for (let t; (t = w.nextNode());) if (t.nodeValue.includes("\u200b")) t.nodeValue = t.nodeValue.replace(/\u200b/g, "");
    return MD.toMd(box);
  };
  const same = (c) => { const m = meta.get(c); return !!m && m.snap === c.outerHTML; };
  const hasContent = (c) => (same(c) ? !!meta.get(c).src : mdOf([c]) !== "");
  function decorate(n) {  // what only the editor needs: ticks to click, a panel's kind to change, mentions kept whole
    n.querySelectorAll(".visually-hidden").forEach((x) => x.remove());
    n.querySelectorAll("li.task").forEach((li) => {
      let box = li.querySelector(":scope > .box");
      if (!box) { box = el("span", { class: "box" }); li.prepend(box); li.classList.remove("done"); }
      const done = li.classList.contains("done");
      box.className = "box" + (done ? " done" : ""); box.textContent = done ? "✓" : "";
      box.setAttribute("contenteditable", "false"); box.setAttribute("role", "checkbox");
      box.setAttribute("aria-checked", String(done)); box.removeAttribute("aria-hidden"); box.title = "Tick or untick";
    });
    n.querySelectorAll(".callout-title").forEach((t) => { t.setAttribute("contenteditable", "false"); t.title = "Change the kind of panel"; });
    n.querySelectorAll("[data-mention]").forEach((m) => m.setAttribute("contenteditable", "false"));
  }
  function renderSurface(t) {
    const nodes = MD.blocks(t, titles).map((s) => nodeOf(s.html)).filter(Boolean);
    surface.replaceChildren(...nodes);
    reconcile(t);
  }
  // the markdown the surface says now
  function serialize() {
    const parts = [];
    let run = [], runGap = null, dirty = false;
    const endRun = () => {
      if (!run.length) return;
      const md = mdOf(run);
      if (md) parts.push({ gap: runGap, src: md, changed: true });
      run = []; runGap = null; dirty = true;
    };
    for (const c of surface.childNodes) {
      if (c.nodeType === 1 && same(c)) {
        endRun();
        const m = meta.get(c);
        if (m.src) { parts.push({ gap: m.gap, src: m.src, changed: dirty }); dirty = false; }
      } else {
        if (c.nodeType === 3 && !c.nodeValue.trim()) continue;
        if (c.nodeType !== 1 && c.nodeType !== 3) continue;
        if (!run.length) runGap = (meta.get(c) || {}).gap ?? null;
        run.push(c);
      }
    }
    endRun();
    if (!parts.length) return lead + tail;
    let out = "";
    parts.forEach((p, i) => {
      let g = p.gap;
      if (i === 0) g = lead;
      else if (g === null || g === "" || ((p.changed || parts[i - 1].changed) && !blank(g))) g = "\n\n";
      out += g + p.src;
    });
    return out + (dirty && tail === "" ? "" : tail);
  }
  // after a change: each element of the surface stands for one block of ``t`` again
  function reconcile(t) {
    const segs = MD.blocks(t, titles), kids = [...surface.children];
    const content = kids.filter(hasContent);
    const ok = content.length === segs.length && content.every((c, i) => c.tagName === tagOf(segs[i].html))
      && ![...surface.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim());
    if (!ok) {  // the markdown reads differently from what is on the screen: draw it as it reads
      const at = shownOffset();
      surface.replaceChildren(...segs.map((s) => nodeOf(s.html)).filter(Boolean));
      segs.forEach((s, i) => { const c = surface.children[i]; decorate(c);
        meta.set(c, { src: t.slice(s.from, s.to), gap: t.slice(i ? segs[i - 1].to : 0, s.from), from: s.from, snap: c.outerHTML }); });
      placeholder();
      if (at !== null) setShownOffset(at);
    } else {
      for (const c of kids) {
        const i = content.indexOf(c);
        if (!same(c)) decorate(c);
        if (i < 0) { meta.set(c, { src: "", gap: "", from: -1, snap: c.outerHTML }); continue; }
        const s = segs[i];
        meta.set(c, { src: t.slice(s.from, s.to), gap: t.slice(i ? segs[i - 1].to : 0, s.from), from: s.from, snap: c.outerHTML });
      }
      placeholder();
    }
    lead = segs.length ? t.slice(0, segs[0].from) : "";
    tail = segs.length ? t.slice(segs[segs.length - 1].to) : t;
  }
  function placeholder() {  // always a paragraph to type in at the end (after a table, a panel, a list)
    const last = surface.lastElementChild;
    if (!last || last.tagName !== "P" || last.classList.contains("callout-title")) {
      const p = el("p", {}, el("br"));
      surface.append(p);
      meta.set(p, { src: "", gap: "", from: -1, snap: p.outerHTML });
    }
  }
  function syncSurface() {
    if (!surface || mode !== "visual" || composing || stopped) return;
    const now = serialize();
    if (now !== text) local(OT.diff(text, now));
    reconcile(text);
  }
  // someone else's change, already applied to the text: redraw the blocks it touched, and only those
  function patchSurface(op, t) {
    const segs = MD.blocks(t, titles);
    const content = [...surface.children].filter((c) => (meta.get(c) || {}).src);
    const srcOf = (s) => t.slice(s.from, s.to);
    let p = 0, q = 0;
    while (p < content.length && p < segs.length && meta.get(content[p]).src === srcOf(segs[p])) p++;
    while (q < content.length - p && q < segs.length - p
      && meta.get(content[content.length - 1 - q]).src === srcOf(segs[segs.length - 1 - q])) q++;
    const gone = content.slice(p, content.length - q);
    const sel = getSelection();
    let keep = null;
    if (sel.rangeCount && surface.contains(sel.anchorNode) && gone.some((g) => g.contains(sel.anchorNode) || g.contains(sel.focusNode))) {
      const [a, b] = caretMd();
      keep = [OT.transformIndex(op, a, true), OT.transformIndex(op, b, true)];
    }
    const before = gone[0] || (p ? content[p - 1].nextSibling : surface.firstChild);
    for (const s of segs.slice(p, segs.length - q)) { const n = nodeOf(s.html); if (n) surface.insertBefore(n, before); }
    gone.forEach((g) => g.remove());
    reconcile(t);
    if (keep) setCaretMd(keep);
  }
  // where the caret is, in the markdown, and back
  function blockOf(node) { while (node && node.parentNode !== surface) node = node.parentNode; return node; }
  function mdAt(node, off) {
    let blk;
    if (node === surface) { blk = surface.childNodes[off] || null; if (blk) { node = blk; off = 0; } }
    else blk = blockOf(node);
    if (!blk) return text.length - tail.length;
    let m = meta.get(blk);
    if (!m || !m.src) {
      let prev = blk.previousSibling;
      while (prev && !(meta.get(prev) || {}).src) prev = prev.previousSibling;
      if (!prev) return lead.length;
      m = meta.get(prev);
      return m.from + m.src.length;
    }
    const r = document.createRange();
    r.setStart(blk, 0);
    try { r.setEnd(node, off); } catch (_) { return m.from; }
    const f = r.cloneContents();
    f.querySelectorAll(".box,.callout-title").forEach((x) => x.remove());
    return m.from + indexOfShown(m.src, f.textContent.length);
  }
  function caretMd() {
    if (mode === "code") return [area.selectionStart, area.selectionEnd];
    const s = getSelection();
    if (!s.rangeCount || !surface.contains(s.anchorNode)) return [0, 0];
    const r = s.getRangeAt(0);
    const a = mdAt(r.startContainer, r.startOffset);
    return [a, r.collapsed ? a : mdAt(r.endContainer, r.endOffset)];
  }
  function domAt(off) {
    const content = [...surface.children].filter((c) => (meta.get(c) || {}).src);
    let blk = content[0] || null;
    for (const c of content) { if (meta.get(c).from <= off) blk = c; else break; }
    if (!blk) return [surface.firstChild || surface, 0];
    const m = meta.get(blk);
    let vis = shownBefore(m.src, Math.max(0, Math.min(off - m.from, m.src.length)));
    const walk = document.createTreeWalker(blk, NodeFilter.SHOW_TEXT,
      { acceptNode: (n) => (n.parentNode.closest(".box,.callout-title") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    let last = null;
    for (let n; (n = walk.nextNode());) {
      const len = n.nodeValue.length;
      if (vis <= len) {
        const ce = n.parentNode.closest("[contenteditable=false]");
        if (ce && blk.contains(ce)) return [ce.parentNode, [...ce.parentNode.childNodes].indexOf(ce) + 1];
        return [n, vis];
      }
      vis -= len; last = n;
    }
    return last ? [last, last.nodeValue.length] : [blk, 0];
  }
  function setCaretMd([a, b]) {
    const r = document.createRange();
    try { r.setStart(...domAt(a)); r.setEnd(...domAt(b === undefined ? a : b)); } catch (_) { return; }
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  function shownOffset() {  // the caret as a count of the words shown before it, over the whole surface
    const s = getSelection();
    if (!s.rangeCount || !surface.contains(s.anchorNode)) return null;
    const r = document.createRange(); r.setStart(surface, 0);
    try { r.setEnd(s.anchorNode, s.anchorOffset); } catch (_) { return null; }
    const f = r.cloneContents(); f.querySelectorAll(".box,.callout-title").forEach((x) => x.remove());
    return f.textContent.length;
  }
  function setShownOffset(vis) {
    const walk = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT,
      { acceptNode: (n) => (n.parentNode.closest(".box,.callout-title,[contenteditable=false]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    let last = null;
    for (let n; (n = walk.nextNode());) {
      if (vis <= n.nodeValue.length) { const r = document.createRange(); r.setStart(n, vis); r.collapse(true);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; }
      vis -= n.nodeValue.length; last = n;
    }
    const r = document.createRange();
    if (last) r.setStart(last, last.nodeValue.length); else r.setStart(surface, 0);
    r.collapse(true);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }

  // ---- typing in the visual view
  const cmd = (name, arg) => { surface.focus(); document.execCommand(name, false, arg); syncSurface(); refreshBar(); };
  const inTag = (re) => {
    const s = getSelection();
    let n = s.rangeCount ? s.anchorNode : null;
    while (n && n !== surface) { if (n.nodeType === 1 && re.test(n.tagName)) return n; n = n.parentNode; }
    return null;
  };
  const SHORT = { "#": "h2", "##": "h2", "###": "h3", "####": "h4", "-": "ul", "*": "ul", "1.": "ol", ">": "quote",
    "```": "pre", "[]": "task", "[ ]": "task" };
  function onVInput(ev) {
    if (composing || stopped) return;
    {  // a place-holding space goes once something is typed beside it (or Enter would split the block before it)
      const s = getSelection(), n = s.anchorNode;
      if (n && n.nodeType === 3 && n.nodeValue.length > 1 && n.nodeValue.includes("\u200b")) {
        const at = s.anchorOffset - (n.nodeValue.slice(0, s.anchorOffset).match(/\u200b/g) || []).length;
        n.nodeValue = n.nodeValue.replace(/\u200b/g, "");
        if (n.nodeValue) { const r = document.createRange(); r.setStart(n, Math.min(at, n.nodeValue.length)); r.collapse(true); s.removeAllRanges(); s.addRange(r); }
      }
    }
    if (surface.firstChild && surface.firstChild.nodeType === 3 && surface.firstChild.nodeValue.trim())
      document.execCommand("formatBlock", false, "p");  // typed into the empty page: a paragraph
    if (ev && ev.inputType === "insertText" && ev.data === " ") {  // markdown typed at the start of a line formats it
      const s = getSelection(), n = s.anchorNode;
      if (n && n.nodeType === 3 && !inTag(/^(LI|PRE|TD|TH)$/)) {
        const before = n.nodeValue.slice(0, s.anchorOffset).replace(/ /g, " "), key = before.trim();
        const blk = blockOf(n), first = blk && blk.tagName === "P" && blk.firstChild === n;
        if (first && before === key + " " && SHORT[key]) {
          n.nodeValue = n.nodeValue.slice(s.anchorOffset) || "​";
          const r = document.createRange(); r.setStart(n, 0); r.collapse(true); s.removeAllRanges(); s.addRange(r);
          act(SHORT[key]);
          return;
        }
      }
    }
    syncSurface();
    if (ev && ev.inputType === "insertText" && ev.data === "/") vSlash();
    else if (vMenu) renderMenu();
  }
  function onVKey(ev) {
    if (vMenu && menuKey(ev)) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (mod && ev.key.toLowerCase() === "k") { ev.preventDefault(); openAsk("link"); return; }
    if (ev.key === "[" && !mod) {  // [[ links a page
      const s = getSelection(), n = s.anchorNode;
      if (n && n.nodeType === 3 && n.nodeValue.slice(0, s.anchorOffset).endsWith("[")) {
        ev.preventDefault(); document.execCommand("delete"); openAsk("page"); return;
      }
    }
    if (ev.key === "Tab") {
      const cell = inTag(/^(TD|TH)$/);
      if (cell) { ev.preventDefault(); nextCell(cell, ev.shiftKey); return; }
    }
    if (ev.key === "Enter" && !ev.shiftKey && !mod) {
      const pre = inTag(/^PRE$/);
      if (pre) {
        ev.preventDefault();
        const s = getSelection(), n = s.anchorNode, at = s.anchorOffset;
        const atEnd = n && n.nodeType === 3 && at === n.nodeValue.length && /\n​?$/.test(pre.textContent) && !n.nextSibling;
        if (atEnd) {  // Enter on an empty last line leaves the code block
          n.nodeValue = n.nodeValue.replace(/\n​?$/, "");
          const p = el("p", {}, el("br")); pre.after(p);
          const r = document.createRange(); r.setStart(p, 0); r.collapse(true); s.removeAllRanges(); s.addRange(r);
        } else document.execCommand("insertText", false, "\n");
        syncSurface();
      }
    }
  }
  function onVClick(ev) {
    const box = ev.target.closest(".box");
    if (box && surface.contains(box)) {
      const li = box.closest("li");
      li.classList.toggle("done"); decorate(li.parentNode);
      syncSurface(); return;
    }
    const title = ev.target.closest(".callout-title");
    if (title && surface.contains(title)) {
      const aside = title.closest("aside"), k = KINDS.find((x) => aside.classList.contains(x)) || "note";
      const nk = KINDS[(KINDS.indexOf(k) + 1) % KINDS.length];
      aside.classList.remove(k); aside.classList.add(nk); title.textContent = KIND_WORD[nk];
      syncSurface(); announce("A " + KIND_WORD[nk].toLowerCase() + " panel");
    }
  }
  function nextCell(cell, back) {
    const cells = [...cell.closest("table").querySelectorAll("th,td")];
    let i = cells.indexOf(cell) + (back ? -1 : 1);
    if (i >= cells.length) {  // Tab in the last cell: a new row
      const row = cell.closest("tr"), fresh = el("tr", {}, ...[...row.children].map(() => el("td", {}, el("br"))));
      (row.parentNode.tagName === "THEAD" ? cell.closest("table").tBodies[0] || cell.closest("table").createTBody() : row.parentNode).append(fresh);
      syncSurface();
      i = cells.length;
      cells.push(...fresh.children);
    }
    const t = cells[Math.max(0, i)];
    const r = document.createRange(); r.selectNodeContents(t); r.collapse(false);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  // a block after the one the caret is in (or instead of it, when it is empty), with the caret in ``focus``
  function putBlock(node, focus) {
    const s = getSelection();
    let blk = s.rangeCount && surface.contains(s.anchorNode) ? blockOf(s.anchorNode) : surface.lastElementChild;
    if (blk && blk.nodeType !== 1) blk = null;
    if (blk && blk.tagName === "P" && !blk.textContent.replace(/​/g, "").trim() && !blk.querySelector("[data-mention]")) blk.replaceWith(node);
    else if (blk) blk.after(node);
    else surface.append(node);
    decorate(node);
    const r = document.createRange();
    const at = focus || node;
    r.selectNodeContents(at); r.collapse(true);
    s.removeAllRanges(); s.addRange(r);
    syncSurface();
  }
  function makeTask() {
    let li = inTag(/^LI$/);
    if (!li) { document.execCommand("insertUnorderedList"); li = inTag(/^LI$/); }
    if (!li) return;
    if (li.classList.contains("task")) { li.classList.remove("task", "done"); li.querySelector(":scope > .box")?.remove(); }
    else { li.classList.add("task"); decorate(li.parentNode); }
    syncSurface();
  }
  const PRE_EMPTY = "​";
  function act(what) {  // the visual view's insertions and block styles
    surface.focus();
    if (what === "h2" || what === "h3" || what === "h4") cmd("formatBlock", `<${what}>`);
    else if (what === "p") {
      if (inTag(/^(UL|OL)$/)) cmd(inTag(/^OL$/) ? "insertOrderedList" : "insertUnorderedList");
      cmd("formatBlock", "<p>");
    } else if (what === "quote") cmd("formatBlock", "<blockquote>");
    else if (what === "pre") {
      const blk = blockOf(getSelection().anchorNode);
      if (blk && blk.tagName === "P" && blk.textContent.replace(/​/g, "").trim()) cmd("formatBlock", "<pre>");
      else { const code = el("code", {}, PRE_EMPTY); putBlock(el("pre", {}, code), code); }
    }
    else if (what === "ul") cmd("insertUnorderedList");
    else if (what === "ol") cmd("insertOrderedList");
    else if (what === "task") makeTask();
    else if (what === "hr") {  // a rule, and a paragraph after it to go on in
      const hr = el("hr"), p = el("p", {}, el("br"));
      putBlock(hr); hr.after(p);
      const r = document.createRange(); r.setStart(p, 0); r.collapse(true);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      syncSurface();
    }
    else if (what === "table") {
      const first = el("td", {}, el("br"));
      putBlock(el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Column"), el("th", {}, "Column"))),
        el("tbody", {}, el("tr", {}, first, el("td", {}, el("br"))))), first);
    } else if (KINDS.includes(what)) {
      const p = el("p", {}, el("br"));
      putBlock(el("aside", { class: "callout " + what, role: "note" }, el("p", { class: "callout-title" }, KIND_WORD[what]), p), p);
    } else if (what === "at") { cmd("insertText", (/\S$/.test(textBefore()) ? " " : "") + "@"); }
    else if (what === "page" || what === "link") openAsk(what);
  }
  const textBefore = () => { const s = getSelection(), n = s.anchorNode; return n && n.nodeType === 3 ? n.nodeValue.slice(0, s.anchorOffset) : ""; };
  function insert(what) {  // a toolbar button, in either view
    if (mode === "visual") { act(what); return; }
    const it = SLASH.find((x) => x.act === what);
    area.focus();
    if (what === "ul" || what === "ol" || what === "task") { linePrefix(it.md); return; }
    if (what === "link") { link(); return; }
    const a = area.selectionStart, s = lineStart(a), blockish = !["at", "page"].includes(what);
    const words = (blockish && a > s ? "\n\n" : "") + it.md;
    replace(a, area.selectionEnd, words);
    if (it.back) { const c = a + words.length - it.back; area.setSelectionRange(c, c); }
    if (what === "at") area.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function inlineMark(mark) {
    if (mode === "code") { area.focus(); wrapSel(mark); return; }
    if (mark === "**") cmd("bold");
    else if (mark === "*") cmd("italic");
    else if (mark === "~~") cmd("strikeThrough");
    else {
      const t = getSelection().toString().replace(/[`\n]/g, "");
      cmd("insertHTML", "<code>" + MD.esc(t || "code") + "</code>​");
    }
  }
  function blockStyle(v) {
    if (mode === "code") {
      area.focus();
      if (v === "pre") { insert("pre"); return; }
      linePrefix({ p: "", h2: "## ", h3: "### ", h4: "#### ", quote: "> " }[v]);
      return;
    }
    act(v);
  }
  function refreshBar() {
    if (mode !== "visual") return;
    for (const b of wrap.querySelectorAll("[data-mark]")) {
      let on = false;
      try { on = document.queryCommandState(b.dataset.mark); } catch (_) { on = false; }
      b.setAttribute("aria-pressed", String(on));
    }
    const t = inTag(/^(H2|H3|H4|BLOCKQUOTE|PRE)$/);
    styleSel.value = t ? ({ BLOCKQUOTE: "quote" }[t.tagName] || t.tagName.toLowerCase()) : "p";
  }
  // a paste or a drop goes through markdown: what can't be said in it keeps only its words
  function insertFrom(dt) {
    const html = dt.getData("text/html"), plain = dt.getData("text/plain");
    let md = plain;
    if (html) md = MD.toMd(new DOMParser().parseFromString(html, "text/html").body);
    if (!md) return;
    if (!html && !/\n/.test(md)) document.execCommand("insertText", false, md);
    else document.execCommand("insertHTML", false, MD.toHtml(md, titles));
    syncSurface();
  }

  // ---- asking for an address or a page: a small bar under the toolbar, never a browser dialog
  let saved = null, askKind = "";
  function openAsk(kind) {
    const s = getSelection();
    saved = s.rangeCount && surface.contains(s.anchorNode) ? s.getRangeAt(0).cloneRange() : null;
    askKind = kind;
    askLabel.textContent = kind === "link" ? "Link to (https://…, mailto: or a page in this hub)" : "Page title, or Type/id";
    askIn.setAttribute("aria-label", askLabel.textContent);
    if (kind === "page") askIn.setAttribute("list", "live-titles"); else askIn.removeAttribute("list");
    askIn.value = kind === "link" ? "https://" : "";
    ask.hidden = false; askIn.focus(); askIn.select();
  }
  function closeAsk(quiet) {
    if (!ask || ask.hidden) return;
    ask.hidden = true; askHint.textContent = "";
    if (quiet) return;
    surface.focus();
    if (saved) { const s = getSelection(); s.removeAllRanges(); s.addRange(saved); }
  }
  function finishAsk() {
    const v = askIn.value.trim();
    if (!v) { closeAsk(); return; }
    if (askKind === "link") {
      if (!/^(https?:\/\/[^\s"'<>\\]+|mailto:[^\s"'<>\\]+|\/(?![\/\\])[^\s"'<>\\]*)$/i.test(v)) {
        askHint.textContent = "Use an address starting https://, mailto: or / (a page in this hub)."; return;
      }
      closeAsk();
      if (saved && !saved.collapsed) cmd("createLink", v);
      else cmd("insertHTML", `<a href="${MD.esc(v)}">${MD.esc(v)}</a>​`);
    } else {
      const src = v.replace(/[\[\]\n|]/g, "").slice(0, 200);
      closeAsk();
      cmd("insertHTML", `<span class="mention" contenteditable="false" data-mention="${MD.esc(src)}">${MD.esc(titles[src] || src)}</span>&nbsp;`);
    }
  }

  // ---- the slash menu, in either view
  let menuAt = -1, menuIdx = 0, menuItems = SLASH, vMenu = null;  // vMenu: the "/" typed in the visual view
  function ensureMenu() {
    if (!menu) {
      menu = el("ul", { class: "live-menu slash-menu", role: "listbox", id: "live-menu", "aria-label": "Insert", hidden: true });
      document.body.append(menu);
    }
  }
  function openMenu(fromButton) {
    ensureMenu();
    if (mode === "visual") {
      if (fromButton) { surface.focus(); const s = getSelection();
        if (!s.rangeCount || !surface.contains(s.anchorNode)) setCaretMd([text.length, text.length]);
        document.execCommand("insertText", false, (/\S$/.test(textBefore()) ? " " : "") + "/"); }
      return;  // the input it makes opens the menu
    }
    if (fromButton) {  // the toolbar's "Insert…": as if / were typed on a new line
      area.focus();
      const at = area.selectionStart, s = lineStart(at);
      replace(at, at, (at > s ? "\n" : "") + "/");
    }
    menuAt = area.selectionStart - 1;
    menuIdx = 0;
    renderMenu();
  }
  function vSlash() {  // "/" at the start of a block, or after a space
    const s = getSelection(), n = s.anchorNode;
    if (!n || n.nodeType !== 3) return;
    const before = n.nodeValue.slice(0, s.anchorOffset - 1).replace(/[ ​]/g, " ");
    if (before && !/\s$/.test(before)) return;
    ensureMenu();
    vMenu = { node: n, at: s.anchorOffset - 1 };
    menuIdx = 0;
    renderMenu();
  }
  function menuQuery() {
    if (vMenu) {
      const s = getSelection();
      if (s.anchorNode !== vMenu.node || vMenu.node.nodeValue[vMenu.at] !== "/" || s.anchorOffset <= vMenu.at) return null;
      return vMenu.node.nodeValue.slice(vMenu.at + 1, s.anchorOffset);
    }
    if (area.value[menuAt] !== "/" || area.selectionStart <= menuAt) return null;
    return area.value.slice(menuAt + 1, area.selectionStart);
  }
  function renderMenu() {
    const q0 = menuQuery();
    if (q0 === null || /\s/.test(q0) || q0.length > 24) { closeMenu(); return; }
    const q = q0.toLowerCase();
    menuItems = SLASH.filter((x) => (x.label + " " + x.keys).toLowerCase().includes(q));
    if (!menuItems.length) { closeMenu(); return; }
    menuIdx = Math.min(menuIdx, menuItems.length - 1);
    menu.replaceChildren(...menuItems.map((x, i) => el("li", { role: "option", id: "live-opt-" + i,
      "aria-selected": i === menuIdx ? "true" : "false", onmousedown: (ev) => { ev.preventDefault(); menuIdx = i; choose(); } },
      icon(x.act === "h2" || x.act === "h3" ? "page" : ICON[x.act] ? x.act : KINDS.includes(x.act) ? "note" : x.act === "quote" ? "note" : "slash"),
      el("span", {}, x.label))));
    let top, left;
    if (vMenu) {
      const r = document.createRange(); r.setStart(vMenu.node, vMenu.at); r.collapse(true);
      const b = r.getClientRects()[0] || vMenu.node.parentNode.getBoundingClientRect();
      top = b.bottom + 6; left = b.left;
    } else {
      const r = caretBox(area, mirror, area.selectionStart), b = area.getBoundingClientRect();
      top = b.top + r.top + r.h + 4; left = b.left + r.left;
    }
    menu.hidden = false;
    const h = menu.offsetHeight, w = menu.offsetWidth;
    if (top + h > innerHeight - 8) top = Math.max(8, top - h - 30);
    menu.style.top = top + "px";
    menu.style.left = Math.max(8, Math.min(left, innerWidth - w - 8)) + "px";
    const host = vMenu ? surface : area;
    host.setAttribute("aria-controls", "live-menu");
    host.setAttribute("aria-activedescendant", "live-opt-" + menuIdx);
    announce(menuItems.length + " choices: " + menuItems[menuIdx].label);
  }
  function closeMenu() {
    if (menu) menu.hidden = true;
    menuAt = -1; vMenu = null;
    area && area.removeAttribute("aria-activedescendant");
    surface && surface.removeAttribute("aria-activedescendant");
  }
  function choose() {
    const it = menuItems[menuIdx];
    if (vMenu) {
      const { node, at } = vMenu, s = getSelection(), end = s.anchorOffset;
      closeMenu();
      const r = document.createRange(); r.setStart(node, at); r.setEnd(node, end);
      s.removeAllRanges(); s.addRange(r);
      document.execCommand("delete");
      act(it.act);
      return;
    }
    const from = menuAt, to = area.selectionStart;
    closeMenu();
    replace(from, to, it.md);
    if (it.back) { const c = from + it.md.length - it.back; area.setSelectionRange(c, c); }
    area.focus();
    if (it.act === "at") area.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function menuKey(ev) {
    if (!menu || menu.hidden) return false;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      menuIdx = (menuIdx + (ev.key === "ArrowDown" ? 1 : menuItems.length - 1)) % menuItems.length;
      renderMenu(); return true;
    }
    if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); choose(); return true; }
    if (ev.key === "Escape") { ev.preventDefault(); closeMenu(); return true; }
    return false;
  }

  // ---- sending: one operation in flight, the rest gathered until it is acknowledged
  async function flush() {
    if (sending || inflight || !buffer || stopped) return;
    inflight = buffer; buffer = null; inflightCid = newCid();
    sending = true;
    setStatus("Saving…");
    for (let tries = 0; !stopped; tries++) {
      try {
        await call("POST", base + "/ops", { cid: inflightCid, base: seq, op: inflight,
          caret: buffer ? undefined : caretMd() });
        break;
      } catch (err) {
        if (err.status === 403 || err.status === 404 || err.status === 422) { stop(err.message); break; }
        setStatus("Not connected: trying again", "warn");  // the same id again: the server keeps it once
        await sleep(Math.min(8000, 500 * 2 ** tries));
      }
    }
    sending = false;
  }
  function receive(o) {
    if (o.seq <= seq) return;
    if (editing && mode === "visual") syncSurface();  // what was typed here first, so it is transformed with the rest
    if (inflight && o.cid === inflightCid) {  // ours, numbered
      seq = o.seq; inflight = null; inflightCid = null;
      if (buffer) flush(); else setStatus("Saved");
      return;
    }
    let op = o.op;
    if (inflight) [inflight, op] = OT.transform(inflight, op);
    if (buffer) [buffer, op] = OT.transform(buffer, op);
    const old = text;
    text = OT.apply(op, text);
    seq = o.seq;
    if (editing && mode === "code") {
      const a = OT.transformIndex(op, area.selectionStart, true), b = OT.transformIndex(op, area.selectionEnd, true);
      const top = area.scrollTop;
      area.value = text;
      area.setSelectionRange(a, b);
      area.scrollTop = top;
      drawCarets();
    } else if (editing) {
      patchSurface(op, text);
      drawCarets();
    } else {
      rendered = patch(article, old, text, titles, rendered);
    }
  }

  // ---- presence
  let caretTimer = null, lastCaret = "";
  function sendCaretSoon() {
    if (menuAt >= 0) renderMenu();
    clearTimeout(caretTimer);
    caretTimer = setTimeout(() => {
      if (!editing || inflight || buffer) return;
      const [a, b] = caretMd(), k = a + ":" + b;
      if (k === lastCaret) return;
      lastCaret = k;
      call("POST", base + "/caret", { at: a, end: b, seq }).catch(() => {});
    }, 250);
  }
  function drawCarets() {
    if (!editing || !lastMembers) return;
    carets.replaceChildren(); vcarets.replaceChildren();
    const box = surfWrap.getBoundingClientRect();
    for (const m of lastMembers) {
      if (m.id === cfg.me || typeof m.caret !== "number") continue;
      let at = m.caret;
      if (inflight) at = OT.transformIndex(inflight, at);
      if (buffer) at = OT.transformIndex(buffer, at);
      at = Math.min(at, text.length);
      const c = cfg.colors[m.color % cfg.colors.length];
      let pos;
      if (mode === "code") {
        const r = caretBox(area, mirror, at);
        if (r.top < -r.h || r.top > area.clientHeight) continue;
        pos = { top: r.top, left: r.left, h: r.h, host: carets };
      } else {
        const r = document.createRange();
        try { r.setStart(...domAt(at)); r.collapse(true); } catch (_) { continue; }
        let b = r.getClientRects()[0];
        if (!b) { const n = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentNode; b = n.getBoundingClientRect(); }
        pos = { top: b.top - box.top, left: b.left - box.left, h: b.height || 22, host: vcarets };
      }
      pos.host.append(el("span", { class: "live-caret", style: `top:${pos.top}px;left:${pos.left}px;height:${pos.h}px;--c:${c}` },
        el("span", { class: "live-caret-name" }, m.name)));
    }
  }
  function people(members) {
    const before = new Map((lastMembers || []).map((m) => [m.id, m.name]));
    const after = new Map(members.map((m) => [m.id, m.name]));
    if (lastMembers) {
      for (const [id, n] of after) if (!before.has(id) && id !== cfg.me) announce(n + " started editing");
      for (const [id, n] of before) if (!after.has(id) && id !== cfg.me) announce(n + " stopped editing");
    }
    lastMembers = members;
    if (faceHost) faceHost.replaceChildren(faces(members, cfg, cfg.me));
    const others = members.filter((m) => m.id !== cfg.me);
    if (nowBar) {
      nowBar.hidden = !others.length;
      if (others.length) {
        const names = others.map((m) => m.name);
        const words = names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
        nowBar.replaceChildren(faces(others, cfg, cfg.me), " ", el("b", {}, words),
          (others.length === 1 ? " is" : " are") + (editing ? " editing with you." : " editing this page now."));
      }
    }
    drawCarets();
  }

  // ---- the stream of news
  async function loop() {
    let fails = 0;
    while (!stopped) {
      try {
        const r = await call("GET", `${base}/poll?since=${seq}&pv=${pv}`);
        fails = 0;
        if (r.ended) { ended(r); return; }
        if (r.reset) { start(r); continue; }
        for (const o of r.ops) receive(o);
        pv = r.pv;
        people(r.members || []);
        if (editing && !inflight && !buffer) setStatus(r.dirty ? "Saved for everyone" : "Saved");
      } catch (err) {
        if (err.status === 403) { stop(err.message); return; }
        fails++;
        if (editing) setStatus("Not connected: trying again", "warn");
        if (fails === 2) announce("Connection lost; trying again");
        await sleep(Math.min(10000, 1000 * fails));
      }
    }
  }
  function start(st) {
    state = st; text = st.text; seq = st.seq; pv = st.pv;
    inflight = buffer = null; inflightCid = null;
    rendered = null;
    if (editing) { if (mode === "code") area.value = text; else renderSurface(text); }
    people(st.members || []);
  }
  function readOnly() { area.readOnly = true; surface.setAttribute("contenteditable", "false"); }
  function ended(r) {
    stopped = true;
    if (editing) {
      setStatus("Editing ended", "warn");
      canvas.prepend(el("div", { class: "flash warning live-flash", role: "alert" }, el("p", {}, "Editing ended: " + r.ended + ". "),
        el("p", {}, "What you see here is kept below; copy anything you need, then reload.")));
      readOnly();
      return;
    }
    if (!editing) location.reload();
  }
  function stop(why) {
    stopped = true;
    setStatus("Not saved", "error");
    if (wrap) {
      canvas.prepend(el("div", { class: "flash error live-flash", role: "alert" }, el("p", {}, why),
        el("p", {}, "Your last change was not kept. Copy what you need, then reload the page.")));
      readOnly();
    }
    announce(why);
  }

  // ---- the title, saved as it is typed (a version of the record of its own)
  let savedTitle = cfg.title;
  async function saveTitle() {
    clearTimeout(saveTitle.t);
    if (!titleIn) return;
    const v = titleIn.value.trim();
    if (!v || v === savedTitle) return;
    setStatus("Saving…");
    try {
      const r = await call("POST", `/api/records/${cfg.ref}/title`, { title: v });
      savedTitle = v;
      const w = wrap.querySelector(".live-where-t"); if (w) w.textContent = r.title || v;
      setStatus("Saved");
    } catch (err) { setStatus("Title not saved: " + err.message, "error"); }
  }

  async function edit() {
    if (editing) return;
    let st;
    try { st = await call("POST", base + "/join"); } catch (err) { announce(err.message); return; }
    if (st.mode !== "edit") { announce(st.why || "You may not edit this page."); return; }
    editing = true;
    build();
    start(st);
    setMode(mode, false);
    if (mode === "visual") { surface.focus(); setCaretMd([0, 0]); } else { area.focus(); area.setSelectionRange(0, 0); }
    setStatus("Saved");
    announce((st.members.length > 1 ? "Editing with " + st.members.filter((m) => m.id !== cfg.me).map((m) => m.name).join(", ")
      : "Editing") + ". Press " + (cfg.publish ? cfg.publish.label : "Update") + " when finished.");
    document.querySelectorAll("[data-live-edit]").forEach((b) => { b.hidden = true; });
    if (!polling) { polling = true; loop(); }
  }
  async function settle() {
    if (mode === "visual") syncSurface();
    await saveTitle();
    for (let i = 0; i < 40 && (inflight || buffer) && !stopped; i++) await sleep(150);
  }
  async function finish(move) {
    await settle();
    stopped = true;
    try { await call("POST", base + "/done"); } catch (_) { /* the server saves when the session goes quiet */ }
    if (move) {  // and then the move (Publish): the same form the page's own button sends
      const f = el("form", { method: "post", action: cfg.url + "/move", hidden: true }, el("input", { type: "hidden", name: "to", value: move.to }));
      document.body.append(f); f.submit(); return;
    }
    location.href = cfg.url;
  }
  async function close() {
    await settle();
    stopped = true;
    try { await call("POST", base + "/leave"); } catch (_) { /* it times out */ }
    location.href = cfg.url;
  }
  window.addEventListener("pagehide", () => {
    if (editing && !stopped) navigator.sendBeacon(base + "/leave", new Blob(["{}"], { type: "text/plain" }));
  });

  let polling = false;
  document.querySelectorAll("[data-live-edit]").forEach((b) => b.addEventListener("click", (ev) => {
    ev.preventDefault(); edit();
  }));
  if (cfg.may && /[?&]edit=1/.test(location.search) === false && location.hash === "#live") edit();
  if (cfg.active) {  // someone is in it now: follow along as they type
    call("GET", base + "/state").then((st) => {
      if (editing || !st.active) return;
      const old = text;
      start(st);
      rendered = patch(article, old, text, titles, null);
      polling = true; loop();
    }).catch(() => {});
  }
}

// ---- comments on words of the page
function comments(article) {
  const form = document.getElementById("c-new");
  if (!form) return;
  const plainOf = () => article.textContent.replace(/\s+/g, " ");
  // mark what each open comment is about, where it still is
  const marks = [...document.querySelectorAll(".comments-side .thread:not(.resolved) .c-quote:not(.gone)")];
  for (const q of marks) highlight(article, q.dataset.quote, q.dataset.before, q.closest("[data-comment]").dataset.comment);
  article.addEventListener("click", (ev) => {
    const m = ev.target.closest("mark.c-mark");
    if (!m) return;
    const t = document.getElementById(m.dataset.for);
    if (t) { t.scrollIntoView({ block: "nearest", behavior: "smooth" }); t.classList.add("active"); t.querySelector("summary, button")?.focus();
      setTimeout(() => t.classList.remove("active"), 2000); }
  });
  const pop = el("button", { type: "button", class: "c-pop primary", hidden: true }, "Comment");
  document.body.append(pop);
  let pending = null;
  function check() {
    const s = getSelection();
    if (!s.rangeCount || s.isCollapsed || !article.contains(s.anchorNode) || !article.contains(s.focusNode) || article.hidden) {
      pop.hidden = true; return;
    }
    const quote = s.toString().replace(/\s+/g, " ").trim();
    if (!quote || quote.length > 500) { pop.hidden = true; return; }
    const r = s.getRangeAt(0), pre = document.createRange();
    pre.selectNodeContents(article); pre.setEnd(r.startContainer, r.startOffset);
    const all = plainOf(), at = pre.toString().replace(/\s+/g, " ").length;
    pending = { quote, before: all.slice(Math.max(0, at - 40), at), after: all.slice(at + quote.length, at + quote.length + 40) };
    const box = r.getBoundingClientRect();
    pop.style.top = (window.scrollY + box.bottom + 6) + "px";
    pop.style.left = (window.scrollX + Math.max(8, Math.min(box.left, innerWidth - 120))) + "px";
    pop.hidden = false;
  }
  document.addEventListener("selectionchange", () => { clearTimeout(check.t); check.t = setTimeout(check, 200); });
  pop.addEventListener("mousedown", (ev) => ev.preventDefault());
  pop.addEventListener("click", () => {
    if (!pending) return;
    for (const k of ["quote", "before", "after"]) form.elements[k].value = pending[k];
    const on = form.querySelector(".c-on");
    on.textContent = "On: “" + pending.quote + "”"; on.hidden = false;
    form.querySelector("label").firstChild.nodeValue = "Your comment";
    form.querySelector("[data-c-cancel]").hidden = false;
    pop.hidden = true;
    form.scrollIntoView({ block: "center" });
    form.elements.body.focus();
  });
  form.querySelector("[data-c-cancel]").addEventListener("click", (ev) => {
    for (const k of ["quote", "before", "after"]) form.elements[k].value = "";
    form.querySelector(".c-on").hidden = true; ev.target.hidden = true;
    form.querySelector("label").firstChild.nodeValue = "Comment on the page";
  });
}
// ---- @ to mention a person or an agent: a list of members, agents marked, and who may not read the record said
let atN = 0;
function atPicker(area, ref, place) {  // place(menu): where the list goes, if not under the box
  if (!area || area.dataset.atReady) return;
  area.dataset.atReady = "1";
  const id = "at-menu-" + (++atN);
  const menu = el("ul", { class: "live-menu at-menu", role: "listbox", id, "aria-label": "Mention", hidden: true });
  const say = el("p", { class: "visually-hidden", "aria-live": "polite" });
  area.after(menu, say);
  if (getComputedStyle(area.parentNode).position === "static") area.parentNode.style.position = "relative";
  let at = -1, idx = 0, rows = [], asked = 0;
  const query = () => area.value.slice(at + 1, area.selectionStart);
  function close() { menu.hidden = true; at = -1; area.removeAttribute("aria-activedescendant"); area.removeAttribute("aria-controls"); }
  async function look() {
    const v = area.value, c = area.selectionStart, m = /(^|[\s(])@([a-z0-9_.-]{0,40})$/i.exec(v.slice(0, c));
    if (!m) { close(); return; }
    at = c - m[2].length - 1;
    const n = ++asked;
    let got;
    try { got = await call("GET", "/api/mentions/candidates?ref=" + encodeURIComponent(ref) + "&q=" + encodeURIComponent(m[2])); }
    catch (_) { close(); return; }
    if (n !== asked || at < 0) return;
    rows = got.members || [];
    if (!rows.length) { close(); return; }
    idx = Math.max(0, rows.findIndex((r) => r.may_read));
    draw();
  }
  function draw() {
    menu.replaceChildren(...rows.map((r, i) => el("li", { role: "option", id: id + "-" + i,
      "aria-selected": i === idx ? "true" : "false", "aria-disabled": r.may_read ? "false" : "true",
      class: r.may_read ? "" : "off", onmousedown: (ev) => { ev.preventDefault(); idx = i; pick(); } },
      el("b", {}, r.name), " ", el("span", { class: "muted" }, "@" + r.id),
      r.kind === "agent" ? el("span", { class: "badge agent" }, "agent") : "",
      r.may_read ? "" : el("small", { class: "muted at-why" }, " " + r.why))));
    menu.hidden = false;
    if (place) place(menu);
    else { menu.style.top = (area.offsetTop + area.offsetHeight + 2) + "px"; menu.style.left = area.offsetLeft + "px"; }
    area.setAttribute("aria-controls", id);
    area.setAttribute("aria-activedescendant", id + "-" + idx);
    const r = rows[idx];
    say.textContent = rows.length + " members. " + r.name + (r.kind === "agent" ? ", agent" : "") + (r.may_read ? "" : ", " + r.why);
  }
  function pick() {
    const r = rows[idx];
    if (!r) return;
    if (!r.may_read) { say.textContent = r.why + ": not mentioned."; return; }
    const from = at, to = area.selectionStart;
    close();
    area.setRangeText("@" + r.id + " ", from, to, "end");
    area.dispatchEvent(new Event("input", { bubbles: true }));
    area.focus();
  }
  area.addEventListener("input", () => { clearTimeout(look.t); look.t = setTimeout(look, 120); });
  area.addEventListener("blur", () => setTimeout(close, 150));
  area.addEventListener("keydown", (ev) => {
    if (menu.hidden) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      idx = (idx + (ev.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length; draw();
    } else if (ev.key === "Enter" || ev.key === "Tab") { pick(); }
    else if (ev.key === "Escape") { close(); }
    else return;
    ev.preventDefault(); ev.stopImmediatePropagation();
  }, true);
}
// the same in the visual editor: "@" and a few letters in the text, the list at the caret, "@id " put in its place
function atPickerCE(host, ref) {
  const id = "at-menu-" + (++atN);
  const menu = el("ul", { class: "live-menu at-menu at-float", role: "listbox", id, "aria-label": "Mention", hidden: true });
  const say = el("p", { class: "visually-hidden", "aria-live": "polite" });
  document.body.append(menu, say);
  let spot = null, idx = 0, rows = [], asked = 0;
  function close() { menu.hidden = true; spot = null; host.removeAttribute("aria-activedescendant"); }
  async function look() {
    const s = getSelection(), n = s.anchorNode;
    if (!s.isCollapsed || !n || n.nodeType !== 3 || !host.contains(n) || n.parentNode.closest("pre,code")) { close(); return; }
    const m = /(^|[\s(\u00a0\u200b])@([a-z0-9_.-]{0,40})$/i.exec(n.nodeValue.slice(0, s.anchorOffset));
    if (!m) { close(); return; }
    spot = { node: n, from: s.anchorOffset - m[2].length - 1 };
    const k = ++asked;
    let got;
    try { got = await call("GET", "/api/mentions/candidates?ref=" + encodeURIComponent(ref) + "&q=" + encodeURIComponent(m[2])); }
    catch (_) { close(); return; }
    if (k !== asked || !spot) return;
    rows = got.members || [];
    if (!rows.length) { close(); return; }
    idx = Math.max(0, rows.findIndex((r) => r.may_read));
    draw();
  }
  function draw() {
    menu.replaceChildren(...rows.map((r, i) => el("li", { role: "option", id: id + "-" + i,
      "aria-selected": i === idx ? "true" : "false", "aria-disabled": r.may_read ? "false" : "true",
      class: r.may_read ? "" : "off", onmousedown: (ev) => { ev.preventDefault(); idx = i; pick(); } },
      el("b", {}, r.name), " ", el("span", { class: "muted" }, "@" + r.id),
      r.kind === "agent" ? el("span", { class: "badge agent" }, "agent") : "",
      r.may_read ? "" : el("small", { class: "muted at-why" }, " " + r.why))));
    menu.hidden = false;
    const rg = document.createRange();
    try { rg.setStart(spot.node, spot.from); rg.collapse(true); } catch (_) { close(); return; }
    const b = rg.getClientRects()[0] || spot.node.parentNode.getBoundingClientRect();
    let top = b.bottom + 6;
    if (top + menu.offsetHeight > innerHeight - 8) top = Math.max(8, b.top - menu.offsetHeight - 6);
    menu.style.top = top + "px";
    menu.style.left = Math.max(8, Math.min(b.left, innerWidth - menu.offsetWidth - 8)) + "px";
    host.setAttribute("aria-activedescendant", id + "-" + idx);
    const r = rows[idx];
    say.textContent = rows.length + " members. " + r.name + (r.kind === "agent" ? ", agent" : "") + (r.may_read ? "" : ", " + r.why);
  }
  function pick() {
    const r = rows[idx];
    if (!r || !spot) return;
    if (!r.may_read) { say.textContent = r.why + ": not mentioned."; return; }
    const s = getSelection(), rg = document.createRange();
    rg.setStart(spot.node, spot.from); rg.setEnd(s.anchorNode, s.anchorOffset);
    close();
    s.removeAllRanges(); s.addRange(rg);
    document.execCommand("insertText", false, "@" + r.id + " ");
  }
  host.addEventListener("input", () => { clearTimeout(look.t); look.t = setTimeout(look, 120); });
  host.addEventListener("blur", () => setTimeout(close, 150));
  host.addEventListener("keydown", (ev) => {
    if (menu.hidden) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      idx = (idx + (ev.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length; draw();
    } else if (ev.key === "Enter" || ev.key === "Tab") { pick(); }
    else if (ev.key === "Escape") { close(); }
    else return;
    ev.preventDefault(); ev.stopImmediatePropagation();
  }, true);
}
function highlight(root, quote, before, cid) {
  if (!quote) return;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
  let all = "";
  for (let n; (n = walk.nextNode());) { nodes.push([n, all.length]); all += n.nodeValue; }
  const norm = (s) => s.replace(/\s+/g, " ");
  // find the quote, preferring the place whose words before it match
  let best = -1, bestScore = -1;
  for (let i = all.indexOf(quote); i >= 0; i = all.indexOf(quote, i + 1)) {
    const pre = norm(all.slice(Math.max(0, i - 60), i)), want = norm(before || "").trim();
    let sc = 0;
    while (sc < want.length && pre.trimEnd().endsWith(want.slice(want.length - sc - 1))) sc++;
    if (sc > bestScore) { best = i; bestScore = sc; }
  }
  if (best < 0) return;
  const end = best + quote.length;
  for (const [n, off] of nodes) {
    const s = Math.max(best, off), e = Math.min(end, off + n.nodeValue.length);
    if (s >= e) continue;
    const r = document.createRange();
    r.setStart(n, s - off); r.setEnd(n, e - off);
    const m = el("mark", { class: "c-mark", "data-for": cid, title: "Commented: open the comment" });
    r.surroundContents(m);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const article = document.querySelector("article.page-body[data-live]");
  if (article) {
    let cfg = null;
    try { cfg = JSON.parse(article.dataset.live); } catch (_) { cfg = null; }
    if (cfg) liveEditor(article, cfg);
  }
  const page = document.querySelector("article.page-body");
  if (page) comments(page);
  for (const t of document.querySelectorAll("textarea[data-at]")) atPicker(t, t.dataset.at);
});
})();
"""

JS = JS + LIVE_JS

LIVE_CSS = r"""
/* ---- writing together: the live editor, people in it, comments beside the page, versions compared */
.prose li.task{list-style:none;margin-left:-20px;display:flex;gap:8px;align-items:baseline}
.prose li.task .box{flex:none;display:inline-grid;place-items:center;width:16px;height:16px;border:2px solid var(--border-bold);
border-radius:3px;font-size:11px;line-height:1;position:relative;top:2px}
.prose li.task .box.done{background:var(--brand);border-color:var(--brand);color:var(--brand-fg)}
.prose li.task.done{color:var(--subtle)}
.prose aside.callout{font-size:inherit;color:var(--fg);border-radius:3px;padding:8px 12px;margin:0 0 12px;background:var(--info-bg);border-left:3px solid var(--info-icon)}
.prose aside.callout>:last-child{margin-bottom:0}
.prose aside.callout .callout-title{font-weight:600;margin:0 0 4px;color:var(--info-fg)}
.prose aside.callout.tip{background:var(--success-bg);border-color:var(--success-icon)}.prose aside.callout.tip .callout-title{color:var(--success-fg)}
.prose aside.callout.warning{background:var(--warning-bg);border-color:var(--warning-icon)}.prose aside.callout.warning .callout-title{color:var(--warning-fg)}
.prose aside.callout.important{background:var(--discovery-bg);border-color:var(--discovery)}.prose aside.callout.important .callout-title{color:var(--discovery-fg)}
.kind-badge{display:inline-block;margin-left:6px;padding:0 4px;border-radius:3px;font-size:11px;line-height:16px;font-weight:600;
text-transform:uppercase;background:var(--discovery-bg);color:var(--discovery-fg);vertical-align:1px}
.page-banner{max-width:780px;margin:0 0 16px;padding:8px 12px;border-radius:3px;background:var(--sunken);border:1px solid var(--border);
display:flex;flex-direction:column;gap:6px}
.page-banner p{margin:0}.page-banner .seg{align-self:flex-start}
.seg{display:inline-flex;border:1px solid var(--border-bold);border-radius:3px;overflow:hidden}
.seg a{padding:2px 10px;color:var(--fg);text-decoration:none;font-size:13px}.seg a+a{border-left:1px solid var(--border-bold)}
.seg a[aria-current]{background:var(--selected-bg);color:var(--selected-fg);font-weight:600}
.page-banner form.read-sign{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0}
.page-banner form.read-sign p{flex:1 1 260px}
.live-now{max-width:780px;margin:0 0 12px;display:flex;align-items:center;gap:6px;font-size:13px}
.live-now[hidden]{display:none}
.live-now .av{width:20px;height:20px;font-size:10px;margin-right:-4px;box-shadow:0 0 0 2px var(--sunken)}
.page-with-side{display:grid;grid-template-columns:minmax(0,780px) minmax(220px,300px);gap:32px;align-items:start}
.comments-side{position:sticky;top:72px;max-height:calc(100vh - 96px);overflow:auto;font-size:13px}
.comments-side h2,.page-comments h2{font-size:14px;margin:0 0 8px}
ul.threads{display:flex;flex-direction:column;gap:8px;margin:0 0 12px}
.thread{padding:8px 10px;border:1px solid var(--border);border-radius:3px;background:var(--raised)}
.thread.active{outline:2px solid var(--brand)}.thread.resolved{opacity:.75}
.thread p{margin:0 0 4px}.thread .c-by{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12px}
.thread .c-by .av{width:20px;height:20px;font-size:10px}
.c-quote{margin:0 0 6px;padding:0 0 0 8px;border-left:3px solid var(--warning-icon);color:var(--subtle);font-style:italic}
.c-quote.gone{text-decoration:line-through;border-color:var(--border-bold)}
.c-gone{font-size:12px}
.thread .replies{margin:6px 0 0 12px;padding-left:8px;border-left:1px solid var(--border)}
.thread details.c-reply summary{cursor:pointer;color:var(--link);font-size:12px}
.thread form.inline{display:inline}
.at{font-weight:600;gap:4px}.at .av{width:18px;height:18px;font-size:9px}.at .badge{font-size:10px}
mark.c-mark{background:var(--warning-bg);color:inherit;border-bottom:2px solid var(--warning-icon);cursor:pointer}
.page-comments{max-width:780px;margin-top:32px;padding-top:16px;border-top:1px solid var(--border)}
.c-pop{position:absolute;z-index:30;box-shadow:var(--shadow-overlay)}
.live-people{display:inline-flex;margin-right:4px}.live-people .av{margin-left:-4px;box-shadow:0 0 0 2px var(--raised)}
.live-people .av.me{outline:2px solid var(--brand);outline-offset:1px}
.live-status{font-size:12px;color:var(--subtle);white-space:nowrap}.live-status.warn{color:var(--warning-fg)}
.live-status.error{color:var(--danger-fg)}
/* the editor: the whole window, the page as it reads in the middle of it; a bar of formatting that stays put */
body.live-editing{overflow:hidden}
.live-editor{position:fixed;inset:0;z-index:40;display:flex;flex-direction:column;background:var(--bg)}
.live-head{display:flex;align-items:center;gap:8px;height:56px;flex:none;padding:0 16px;border-bottom:1px solid var(--border);background:var(--raised)}
.live-where{flex:1;min-width:0;display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;font-size:14px}
.live-where-t{overflow:hidden;text-overflow:ellipsis;color:var(--subtle)}
.live-modes button{height:30px;border-radius:0;padding:0 12px;font-size:13px;background:transparent;color:var(--fg)}
.live-modes button+button{border-left:1px solid var(--border-bold)}
.live-modes button[aria-pressed=true]{background:var(--selected-bg);color:var(--selected-fg);font-weight:600}
.live-toolbar{flex:none;display:flex;align-items:center;gap:2px;padding:4px 16px;min-height:44px;overflow-x:auto;
scrollbar-width:thin;background:var(--raised);border-bottom:1px solid var(--border);box-shadow:0 1px 2px rgba(0,0,0,.04)}
.live-toolbar>*{flex:none}
.live-toolbar .md-style{width:auto;height:32px;margin:0 4px 0 0;min-width:9.5em;font-size:13px}
.live-toolbar select:disabled{opacity:.4}
.live-ic{display:inline-grid;place-items:center}.live-ins{display:inline-flex;align-items:center;gap:4px}
.live-ins-w{font-size:13px}
.live-ask{flex:none;padding:8px 16px;background:var(--raised)}
.live-canvas{flex:1;min-height:0;overflow:auto;position:relative}
.live-page{max-width:880px;margin:0 auto;padding:32px 40px 96px;min-height:100%;display:flex;flex-direction:column}
.live-page.code{max-width:none;padding:16px 24px 24px}
.live-flash{max-width:880px;margin:16px auto 0}
input.live-title,h1.live-title{display:block;width:100%;margin:0 0 16px;padding:4px 0;border:0;border-radius:0;background:transparent;
color:var(--fg);font-size:32px;line-height:1.25;font-weight:600;box-shadow:none}
input.live-title:focus{outline:0;box-shadow:inset 0 -2px 0 var(--brand)}
input.live-title::placeholder{color:var(--subtlest,var(--subtle))}
.live-page.code .live-title{font-size:20px;margin-bottom:8px}
.live-surf{position:relative;flex:1}
.live-surf[hidden],.live-code[hidden]{display:none}
.prose.live-surface{max-width:none;margin:0;min-height:60vh;outline:0;padding:0 0 48px;caret-color:var(--brand)}
.live-surface .callout-title{cursor:pointer;user-select:none}
.live-surface li.task .box{cursor:pointer}
.live-surface table td,.live-surface table th{border:1px solid var(--border);min-width:4em}
.live-surface pre{white-space:pre-wrap}
.live-surface p:only-child:empty::before{content:"Type here, or / to insert";color:var(--subtle)}
.live-code{position:relative;flex:1;display:flex;min-height:0}
textarea.live-text{display:block;flex:1;width:100%;min-height:calc(100vh - 200px);border:1px solid var(--border);border-radius:3px;
margin:0;resize:none;padding:16px 20px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;line-height:22px;
background:var(--input-bg);color:var(--fg);white-space:pre-wrap;overflow-wrap:break-word;tab-size:4}
textarea.live-text:focus{outline:2px solid var(--brand);outline-offset:-2px}
.live-mirror{position:absolute;visibility:hidden;top:0;left:0;white-space:pre-wrap;overflow-wrap:break-word}
.live-carets{position:absolute;inset:0;pointer-events:none;overflow:hidden}
.live-caret{position:absolute;width:2px;background:var(--c)}
.live-caret-name{position:absolute;bottom:100%;left:-1px;padding:0 4px;font:600 10px/14px system-ui,sans-serif;color:#fff;
background:var(--c);border-radius:3px 3px 3px 0;white-space:nowrap}
.live-help{font-size:12px;margin:12px 0 0}
.live-page.code .live-help{display:none}
.live-menu.slash-menu,.live-menu.at-float{position:fixed;z-index:50}
.live-surface .mention{color:var(--link);cursor:default}
.slash-menu{min-width:240px;max-height:min(360px,60vh);overflow:auto}
.slash-menu li{display:flex;align-items:center;gap:10px}
.slash-menu .live-ic{width:28px;height:28px;border:1px solid var(--border);border-radius:3px;background:var(--raised)}
@media (max-width:700px){
  .live-head{padding:0 8px;gap:6px}
  .live-where,.live-status{display:none}
  .live-head .live-modes{margin-right:auto}
  .live-toolbar{padding:4px 8px}
  .live-ins-w{display:none}
  .live-page{padding:16px 16px 64px}
  .live-page.code{padding:8px}
  input.live-title,h1.live-title{font-size:24px}
  textarea.live-text{padding:12px;font-size:13px}
}
.live-menu{position:absolute;z-index:30;margin:0;padding:4px 0;min-width:200px;list-style:none;background:var(--overlay);
border-radius:3px;box-shadow:var(--shadow-overlay)}
.at-menu{min-width:260px;max-width:min(420px,90vw);max-height:260px;overflow:auto}
.at-menu li.off{opacity:.65;cursor:not-allowed}.at-menu .badge{margin-left:6px}.at-menu .at-why{display:block}
.c-sug{margin:6px 0;padding:8px;border:1px dashed var(--border-bold);border-radius:6px}
.c-sug .c-change{margin:4px 0}
/* comments on a document (a file the record holds), beside its editor or on the record page */
.doc-comments{font-size:13px;max-width:780px}.doc-comments h2,.doc-comments h3{font-size:14px;margin:0 0 8px}
.doc-comments .c-new textarea{width:100%}.doc-comments .doc-note{margin:0 0 8px;font-size:12px}
ul.threads li.thread{padding:8px 10px}.doc-comments .replies li.by-agent{border-left:3px solid var(--discovery);padding-left:8px}
.doc-comments .thread.by-agent{border-left:3px solid var(--discovery)}
.doc-comments .c-quote-in summary{cursor:pointer;color:var(--link);font-size:12px}
.doc-comments+.doc-comments{margin-top:16px}
.live-menu li{padding:6px 12px;cursor:pointer}.live-menu li[aria-selected=true]{background:var(--selected-bg);color:var(--selected-fg)}
.diff-text{white-space:pre-wrap;font-size:14px;line-height:22px;padding:12px;border:1px solid var(--border);border-radius:3px;background:var(--raised)}
.diff ins,.diff-key ins{background:var(--success-bg);color:var(--success-fg);text-decoration:none;border-bottom:1px solid var(--success-icon)}
.diff del,.diff-key del{background:var(--danger-bg);color:var(--danger-fg)}
@media (max-width:1000px){
  .page-with-side{grid-template-columns:minmax(0,1fr)}
  .comments-side{position:static;max-height:none}
}
"""

CSS = CSS + LIVE_CSS

# The dashboard editor (web/dashboard_pages.py): runs after the main script, which leaves PH.el for it.
DASH_JS = r"""
(function () {
"use strict";
// ------------------------------------------------------------------ the dashboard editor (web/dashboard_pages.py)
// The view is a document (views.doc_of): widgets with a grid place, set up with pickers. Every change is sent to
// /api/views/draft, which writes the TOML, checks it and previews each widget with the editor's own data. The script
// never builds the condition text itself: a filter goes as pickers and the server writes it.
const { el } = window.PH || {};
if (!el) return;

function dashEditor(root) {
  const boot = JSON.parse(document.getElementById("dz-boot").textContent);
  const spec = boot.spec, cat = boot.catalog, COLS = spec.columns, ROW = 56, GAP = 12;
  let doc = boot.doc;
  doc.widget = doc.widget || [];
  let sel = null, mode = "canvas", last = null, timer = null, seq = 0, touched = !!(boot.doc.widget || []).length;
  let idTouched = !!doc.id;
  const keys = new WeakMap(), views = new Map(), asText = new WeakSet();
  let nextKey = 1;
  const keyOf = (w) => { if (!keys.has(w)) keys.set(w, nextKey++); return keys.get(w); };
  const $ = (id) => document.getElementById(id);
  const canvas = $("dz-canvas"), panel = $("dz-panel"), status = $("dz-status"), toml = $("dz-toml");
  const form = $("dz-propose"), main = $("dz-main"), code = $("dz-code"), empty = $("dz-empty");
  const kindOf = (w) => spec.kinds[w.kind] || spec.kinds.table;
  const has = (w, opt) => (kindOf(w).options || []).includes(opt);
  const typeOf = (w) => cat.types.find((t) => t.id === w.type);
  const src = (w) => (w.kind === "links" ? null : w.source || "records");
  const fieldsOf = (w) => (src(w) === "records" ? (typeOf(w) || { fields: [] }).fields : spec.row_fields[src(w)] || []);
  // columns, group_by and sort name a record type's field by its name; a filter reads the stored path
  const colKey = (w, f) => (src(w) === "records" ? f.name : f.path);
  const fieldBy = (w, key) => fieldsOf(w).find((f) => f.name === key || f.path === key);
  const labelOf = (w, key) => { const f = fieldBy(w, key); return f ? f.label : key; };
  const isDate = (f) => f && /^date/.test(f.type);

  // ---- layout: no two widgets overlap; the moved one keeps its place, the others make room, all float up
  const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  function settle(moved) {
    const ws = doc.widget.slice().sort((a, b) => (a === moved ? -1 : b === moved ? 1 : a.grid.y - b.grid.y || a.grid.x - b.grid.x));
    const done = [];
    for (const w of ws) {
      const g = w.grid;
      g.w = Math.max(1, Math.min(COLS, g.w | 0)); g.x = Math.max(0, Math.min(COLS - g.w, g.x | 0));
      g.h = Math.max(1, Math.min(spec.max_height, g.h | 0)); g.y = Math.max(0, g.y | 0);
      while (done.some((d) => overlap(d, g))) g.y++;
      done.push(g);
    }
    const up = doc.widget.slice().sort((a, b) => a.grid.y - b.grid.y || a.grid.x - b.grid.x), above = [];
    for (const w of up) {
      const g = w.grid;
      while (g.y > 0 && !above.some((d) => overlap(d, { ...g, y: g.y - 1 }))) g.y--;
      above.push(g);
    }
  }
  const bottom = () => Math.max(0, ...doc.widget.map((w) => w.grid.y + w.grid.h));
  function metrics() {
    const width = canvas.clientWidth;
    return { colW: (width + GAP) / COLS, rowH: ROW + GAP };
  }

  // ---- the palette
  const glyph = { count: "123", table: "▤", list: "☰", board: "▥", chart: "▮▮", tree: "⌥", links: "↗" };
  const pal = $("dz-palette");
  for (const [k, ks] of Object.entries(spec.kinds)) {
    const b = el("button", { type: "button", class: "dz-kind", draggable: "true", "data-kind": k,
      "aria-label": `Add ${ks.label.toLowerCase()}: ${ks.about}`,
      onclick: () => add(k),
      ondragstart: (ev) => { ev.dataTransfer.setData("text/x-dz-kind", k); ev.dataTransfer.effectAllowed = "copy"; } },
      el("span", { class: "dz-glyph", "aria-hidden": "true" }, glyph[k] || "▢"),
      el("span", { class: "dz-kl" }, el("b", {}, ks.label), el("small", {}, ks.about)));
    pal.append(b);
  }
  canvas.addEventListener("dragover", (ev) => { if (ev.dataTransfer.types.includes("text/x-dz-kind")) { ev.preventDefault(); canvas.classList.add("drop"); } });
  canvas.addEventListener("dragleave", () => canvas.classList.remove("drop"));
  canvas.addEventListener("drop", (ev) => {
    const k = ev.dataTransfer.getData("text/x-dz-kind");
    canvas.classList.remove("drop");
    if (!k) return;
    ev.preventDefault();
    const r = canvas.getBoundingClientRect(), m = metrics();
    add(k, { x: Math.floor((ev.clientX - r.left) / m.colW), y: Math.floor((ev.clientY - r.top) / m.rowH) });
  });

  function defaults(w) {
    const t = cat.types.find((x) => (w.kind === "tree" ? x.tree : true)) || cat.types[0];
    if (kindOf(w).sources.length && !w.type && src(w) === "records" && t) w.type = t.id;
    if ((w.kind === "board" || w.kind === "chart") && !w.group_by) {
      const tt = typeOf(w);
      const f = src(w) === "records" ? (tt && (fieldBy(w, tt.lifecycle || "") || tt.fields.find((x) => x.choices.length)))
        : fieldsOf(w).find((x) => x.name === "status" || x.name === "role");
      if (f) w.group_by = colKey(w, f);
    }
    if (w.kind === "links" && !(w.links || []).length) w.links = cat.pages.length ? [{ ...cat.pages[0] }] : [{ label: "Inbox", href: "/" }];
  }
  function add(kind, at) {
    const ks = spec.kinds[kind], [gw, gh] = ks.size;
    const w = { kind, title: ks.label === "Number" ? "How many" : ks.label };
    w.grid = at ? { x: Math.min(Math.max(0, at.x), COLS - gw), y: Math.max(0, at.y), w: gw, h: gh }
      : { x: 0, y: bottom(), w: gw, h: gh };
    defaults(w);
    doc.widget.push(w);
    settle(w);
    select(w);
    changed();
    const v = views.get(keyOf(w));
    if (v) v.focus();
  }
  function remove(w) {
    doc.widget = doc.widget.filter((x) => x !== w);
    settle(null);
    if (sel === w) select(null);
    changed();
    say(`Removed ${w.title || kindOf(w).label}.`);
  }
  function duplicate(w) {
    const c = JSON.parse(JSON.stringify(w));
    c.grid.y = w.grid.y + w.grid.h;
    c.title = (w.title || kindOf(w).label) + " (copy)";
    doc.widget.push(c);
    settle(c); select(c); changed();
  }

  // ---- the canvas
  function describe(w) {
    const g = w.grid;
    return `${kindOf(w).label}: ${w.title || "untitled"}. Column ${g.x + 1}, row ${g.y + 1}, ${g.w} wide, ${g.h} high.`;
  }
  function place(w) {
    const v = views.get(keyOf(w));
    if (!v) return;
    const m = metrics(), g = w.grid;
    v.style.left = g.x * m.colW + "px"; v.style.top = g.y * m.rowH + "px";
    v.style.width = g.w * m.colW - GAP + "px"; v.style.height = g.h * m.rowH - GAP + "px";
    v.setAttribute("aria-label", describe(w));
  }
  function drawCanvas() {
    const m = metrics(), seen = new Set();
    canvas.style.setProperty("--colw", m.colW + "px");
    canvas.style.setProperty("--rowh", m.rowH + "px");
    canvas.style.height = Math.max(6, bottom() + 3) * m.rowH + "px";
    for (const w of doc.widget) {
      const k = keyOf(w);
      seen.add(k);
      let v = views.get(k);
      if (!v) { v = card(w); views.set(k, v); canvas.append(v); }
      v.classList.toggle("on", w === sel);
      v.querySelector(".dz-title").textContent = w.title || "Untitled";
      v.querySelector(".dz-kindl").textContent = kindOf(w).label;
      place(w);
    }
    for (const [k, v] of views) if (!seen.has(k)) { v.remove(); views.delete(k); }
    empty.hidden = doc.widget.length > 0;
  }
  function card(w) {
    const body = el("div", { class: "dz-body", inert: "" }, el("p", { class: "muted" }, "…"));
    const total = el("span", { class: "dz-total badge" });
    const head = el("div", { class: "dz-head" },
      el("span", { class: "dz-grip", "aria-hidden": "true" }, "⠿"),
      el("span", { class: "dz-kindl" }), el("b", { class: "dz-title" }), total,
      el("button", { type: "button", class: "dz-x subtle", "aria-label": "Remove this widget", title: "Remove",
        onpointerdown: (ev) => ev.stopPropagation(), onclick: (ev) => { ev.stopPropagation(); remove(w); } }, "×"));
    const grip = el("span", { class: "dz-resize", "aria-hidden": "true", title: "Drag to resize" });
    const v = el("div", { class: "dz-w", role: "listitem", tabindex: "0", "data-kind": w.kind }, head, body, grip);
    v.addEventListener("click", () => select(w));
    v.addEventListener("focus", () => { if (sel !== w) select(w, true); });
    v.addEventListener("keydown", (ev) => keys_(ev, w));
    head.addEventListener("pointerdown", (ev) => drag(ev, w, "move"));
    grip.addEventListener("pointerdown", (ev) => drag(ev, w, "size"));
    return v;
  }
  function keys_(ev, w) {
    if (ev.target !== views.get(keyOf(w))) return;
    const g = w.grid, d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
    if (d) {
      ev.preventDefault();
      if (ev.shiftKey) { g.w += d[0]; g.h += d[1]; } else { g.x += d[0]; g.y += d[1]; }
      settle(w); drawCanvas(); drawPanel(); changed(true);
      say(describe(w));
    } else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); remove(w); }
    else if (ev.key === "Escape") { select(null); }
  }
  let dragging = null;
  function drag(ev, w, how) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    select(w, true);
    const v = views.get(keyOf(w)), m = metrics(), start = { ...w.grid }, x0 = ev.clientX, y0 = ev.clientY;
    v.setPointerCapture(ev.pointerId);
    v.classList.add("lifted");
    const ghost = el("div", { class: "dz-ghost", "aria-hidden": "true" });
    canvas.append(ghost);
    dragging = w;
    const showGhost = () => {
      const g = w.grid;
      Object.assign(ghost.style, { left: g.x * m.colW + "px", top: g.y * m.rowH + "px",
        width: g.w * m.colW - GAP + "px", height: g.h * m.rowH - GAP + "px" });
    };
    const moveTo = (e2) => {
      const dx = (e2.clientX - x0) / m.colW, dy = (e2.clientY - y0) / m.rowH, g = w.grid, was = JSON.stringify(g);
      if (how === "move") { g.x = Math.round(start.x + dx); g.y = Math.round(start.y + dy); g.w = start.w; g.h = start.h; }
      else { g.w = Math.round(start.w + dx); g.h = Math.round(start.h + dy); g.x = start.x; g.y = start.y; if (g.x + g.w > COLS) g.w = COLS - g.x; }
      if (JSON.stringify(g) !== was) {
        settle(w);
        for (const o of doc.widget) if (o !== w) place(o);
        canvas.style.height = Math.max(6, bottom() + 3) * m.rowH + "px";
      }
      showGhost();
      // the lifted card follows the pointer; it lands on the ghost's cells
      if (how === "move") { v.style.left = start.x * m.colW + (e2.clientX - x0) + "px"; v.style.top = start.y * m.rowH + (e2.clientY - y0) + "px"; }
      else { v.style.width = Math.max(m.colW - GAP, start.w * m.colW - GAP + (e2.clientX - x0)) + "px";
        v.style.height = Math.max(ROW, start.h * m.rowH - GAP + (e2.clientY - y0)) + "px"; }
    };
    const end = () => {
      v.removeEventListener("pointermove", moveTo); v.removeEventListener("pointerup", end); v.removeEventListener("pointercancel", end);
      v.classList.remove("lifted"); ghost.remove(); dragging = null;
      drawCanvas(); drawPanel();
      if (JSON.stringify(start) !== JSON.stringify(w.grid)) { changed(true); say(describe(w)); }
    };
    showGhost();
    v.addEventListener("pointermove", moveTo);
    v.addEventListener("pointerup", end);
    v.addEventListener("pointercancel", end);
  }
  window.addEventListener("resize", () => { if (mode === "canvas") drawCanvas(); });

  // ---- the side panel
  function select(w, quiet) {
    sel = w;
    drawCanvas();
    drawPanel();
    if (!quiet && w) { const v = views.get(keyOf(w)); if (v && document.activeElement !== v) v.focus({ preventScroll: true }); }
  }
  let uid = 0;
  const nid = () => "dz-f" + (++uid);
  function field(label, control, help) {
    const id = control.id || (control.id = nid());
    return el("div", { class: "dz-field" }, el("label", { for: id }, label), control,
      help ? el("small", { class: "help muted" }, help) : null);
  }
  function input(value, on, attrs) {
    return el("input", { value: value == null ? "" : String(value), ...(attrs || {}),
      oninput: (ev) => on(ev.target.value) });
  }
  function select_(options, chosen, on, blank) {
    const s = el("select", { onchange: (ev) => on(ev.target.value) });
    if (blank !== undefined) s.append(el("option", { value: "" }, blank));
    for (const o of options) {
      const [v, l] = Array.isArray(o) ? o : [o, o];
      const opt = el("option", { value: v }, l);
      if (String(v) === String(chosen ?? "")) opt.selected = true;
      s.append(opt);
    }
    return s;
  }
  function chips(legend, options, chosen, on) {
    const set = new Set(chosen || []);
    return el("fieldset", { class: "choices dz-chips" }, el("legend", {}, legend),
      options.map(([v, l]) => el("label", { class: "radio" },
        el("input", { type: "checkbox", value: v, checked: set.has(v), onchange: (ev) => {
          if (ev.target.checked) set.add(v); else set.delete(v);
          on(options.map((o) => o[0]).filter((x) => set.has(x)));
        } }), l)));
  }
  const set = (w, k, v, redraw) => {
    if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length)) delete w[k]; else w[k] = v;
    changed();
    if (redraw) drawPanel();
    drawCanvas();
  };
  function slug(t) {
    let s = (t || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    if (s && !/^[a-z]/.test(s)) s = "view-" + s;
    return s === "new" ? "new-view" : s;
  }

  function drawPanel() {
    if (dragging) return;
    const focusId = document.activeElement && panel.contains(document.activeElement) ? document.activeElement.id : null;
    panel.textContent = "";
    if (!sel) drawViewPanel(); else drawWidgetPanel(sel);
    if (focusId && $(focusId)) $(focusId).focus();
  }
  function drawViewPanel() {
    const idBox = input(doc.id, (v) => { idTouched = true; set(doc, "id", v.trim()); },
      { id: "dz-id", pattern: "[a-z][a-z0-9-]*", disabled: boot.locked_id, spellcheck: "false" });
    panel.append(el("h2", {}, "The view"),
      el("p", { class: "muted small" }, "Choose a widget on the grid to set it up."),
      field("Title *", input(doc.title, (v) => {
        set(doc, "title", v);
        if (!idTouched && !boot.locked_id) { doc.id = slug(v); idBox.value = doc.id; }
      }, { id: "dz-title", required: true })),
      field("Id", idBox, boot.locked_id ? "A view keeps its id." : "Lower case letters, digits and dashes; it is the view's address."),
      field("Summary", input(doc.summary, (v) => set(doc, "summary", v), { id: "dz-summary" }), "A line under the title."),
      el("label", { class: "radio dz-check" }, el("input", { type: "checkbox", id: "dz-nav", checked: doc.nav !== false,
        onchange: (ev) => set(doc, "nav", ev.target.checked ? undefined : false) }), "Show it in the sidebar"),
      chips("Whose sidebar (none chosen: everyone's)", cat.roles.map((r) => [r.id, r.name]), doc.roles,
        (v) => set(doc, "roles", v)),
      el("p", { class: "muted small" }, "Whoever opens it sees only the records and work they may see."));
  }
  function drawWidgetPanel(w) {
    const ks = kindOf(w), s = src(w);
    const head = el("div", { class: "dz-ph" }, el("h2", {}, ks.label),
      el("button", { type: "button", class: "subtle", onclick: () => select(null) }, "The view"),
      el("button", { type: "button", class: "subtle", onclick: () => duplicate(w) }, "Duplicate"),
      el("button", { type: "button", class: "danger-subtle", onclick: () => remove(w) }, "Remove"));
    panel.append(head, field("Title", input(w.title, (v) => set(w, "title", v), { id: "dz-w-title" })));
    panel.append(field("Kind", select_(Object.entries(spec.kinds).map(([k, x]) => [k, x.label]), w.kind, (v) => {
      w.kind = v;
      const allowed = spec.kinds[v].sources;
      if (allowed.length && !allowed.includes(src(w) || "records")) w.source = allowed[0];
      if (v === "links") for (const k of ["source", "type", "workflow", "role", "status", "where", "filter", "columns", "group_by", "sort", "descending", "limit"]) delete w[k];
      defaults(w);
      changed(); drawCanvas(); drawPanel();
    }, undefined), ks.about));
    if (w.kind === "links") return linksPanel(w);
    if (ks.sources.length > 1) {
      panel.append(field("Shows", select_(ks.sources.map((k) => [k, spec.sources[k]]), s, (v) => {
        for (const k of ["type", "workflow", "role", "status", "where", "filter", "columns", "group_by", "sort"]) delete w[k];
        w.source = v === "records" ? undefined : v;
        if (v === "records") delete w.source;
        defaults(w); changed(); drawCanvas(); drawPanel();
      })));
    }
    if (s === "records") {
      panel.append(field("Record type", select_(cat.types.map((t) => [t.id, t.label]), w.type, (v) => {
        for (const k of ["where", "filter", "columns", "group_by", "sort"]) delete w[k];
        w.type = v; defaults(w); changed(); drawCanvas(); drawPanel();
      }, w.type ? undefined : "Choose a type")));
    } else {
      panel.append(field("Workflow", select_(cat.workflows.map((x) => [x.id, x.title]), w.workflow,
        (v) => set(w, "workflow", v), "Any workflow")));
      const sts = s === "items" ? spec.item_status : spec.run_status;
      panel.append(chips(s === "items" ? "Status (none chosen: open and claimed)" : "Status (none chosen: any)",
        sts.map((x) => [x, x]), w.status, (v) => set(w, "status", v)));
      if (s === "items") panel.append(field("For the role", select_(cat.roles.map((r) => [r.id, r.name]), w.role,
        (v) => set(w, "role", v), "Any role")));
    }
    if (has(w, "filter")) filterPanel(w);
    if (has(w, "group_by")) {
      const fs = fieldsOf(w).filter((f) => f.filter !== false);
      panel.append(field(w.kind === "board" ? "A column for each" : "A bar for each",
        select_(fs.map((f) => [colKey(w, f), f.label]), w.group_by, (v) => set(w, "group_by", v), "Choose a field")));
    }
    if (has(w, "columns")) columnsPanel(w);
    if (has(w, "sort")) {
      const fs = fieldsOf(w).filter((f) => f.filter !== false);
      const box = el("div", { class: "dz-row" },
        select_(fs.map((f) => [colKey(w, f), f.label]), w.sort, (v) => { set(w, "sort", v, true); }, "Newest first"));
      box.firstChild.id = "dz-sort";
      if (w.sort) box.append(el("label", { class: "radio" }, el("input", { type: "checkbox", checked: !!w.descending,
        onchange: (ev) => set(w, "descending", ev.target.checked || undefined) }), "Largest or latest first"));
      panel.append(el("div", { class: "dz-field" }, el("label", { for: "dz-sort" }, "Order by"), box));
    }
    if (has(w, "limit")) panel.append(field("At most", input(w.limit, (v) => set(w, "limit", v === "" ? undefined : Math.max(1, parseInt(v, 10) || 1)),
      { type: "number", min: "1", max: "1000", placeholder: w.kind === "tree" ? "500" : "25", id: "dz-limit" }), "rows shown"));
    placePanel(w);
  }
  function placePanel(w) {
    const g = w.grid, num = (k, label, min, max, off) => field(label, input(g[k] + off, (v) => {
      const n = parseInt(v, 10);
      if (Number.isNaN(n)) return;
      g[k] = n - off; settle(w); drawCanvas(); changed(true);
    }, { type: "number", min: String(min), max: String(max), id: "dz-g-" + k }));
    panel.append(el("fieldset", { class: "dz-place" }, el("legend", {}, "Place on the grid"),
      el("div", { class: "dz-quad" }, num("x", "Column", 1, COLS, 1), num("y", "Row", 1, 200, 1),
        num("w", "Width", 1, COLS, 0), num("h", "Height", 1, spec.max_height, 0)),
      el("small", { class: "muted" }, "Or drag it by its top bar and its corner; on the grid, arrow keys move it and Shift with an arrow resizes it.")));
  }

  // ---- filters, with pickers
  function opsFor(f) {
    const o = spec.ops, all = ["==", "!=", "in", "not in", "<", "<=", ">", ">=", "empty", "set"];
    let ks = all;
    if (!f) ks = all;
    else if (f.choices && f.choices.length || f.type === "role" || f.type === "choice") ks = ["==", "!=", "in", "not in", "empty", "set"];
    else if (f.type === "boolean") ks = ["==", "!=", "empty", "set"];
    else if (f.type === "number" || f.type === "integer" || isDate(f)) ks = ["==", "!=", "<", "<=", ">", ">=", "empty", "set"];
    else ks = ["==", "!=", "in", "not in", "empty", "set"];
    const dated = { "<": "is before", "<=": "is on or before", ">": "is after", ">=": "is on or after" };
    return ks.map((k) => [k, isDate(f) && dated[k] ? dated[k] : o[k]]);
  }
  function choicesOf(f) {
    if (!f) return [];
    if (f.type === "role") return cat.roles.map((r) => [r.id, r.name]);
    return (f.choices || []).map((c) => [c, c]);
  }
  function filterPanel(w) {
    const box = el("fieldset", { class: "dz-filter" }, el("legend", {}, "Only those where"));
    panel.append(box);
    if ((w.where || asText.has(w)) && !w.filter) {
      const ta = el("textarea", { class: "code", rows: "3", id: "dz-where", spellcheck: "false",
        oninput: (ev) => { const v = ev.target.value.trim(); if (v) w.where = v; else delete w.where; changed(); } });
      ta.value = w.where;
      box.append(field("Condition", ta, "Written as a condition: it says more than the pickers can show."),
        el("button", { type: "button", class: "subtle", onclick: () => { delete w.where; asText.delete(w); changed(); drawPanel(); } },
          "Use pickers instead (clears it)"));
      return;
    }
    const f = w.filter || { match: "all", rules: [] };
    const fs = fieldsOf(w).filter((x) => x.filter !== false);
    const commit = (redraw) => { w.filter = f; delete w.where; if (!f.rules.length) { delete w.filter; } changed(); if (redraw) drawPanel(); };
    if (f.rules.length > 1) box.append(field("Match", select_([["all", "all of these"], ["any", "any of these"]], f.match,
      (v) => { f.match = v; commit(); })));
    f.rules.forEach((r, i) => {
      const fd = fs.find((x) => x.path === r.path);
      const fsel = select_(fs.map((x) => [x.path, x.label]), r.path, (v) => {
        f.rules[i] = { path: v, op: "==" };
        const nf = fs.find((x) => x.path === v), ch = choicesOf(nf);
        if (ch.length) f.rules[i].value = ch[0][0]; else if (isDate(nf)) { f.rules[i].op = "<="; f.rules[i].today = ""; }
        else if (nf && nf.type === "boolean") f.rules[i].value = true; else f.rules[i].value = "";
        commit(true);
      });
      fsel.setAttribute("aria-label", `Condition ${i + 1}: field`);
      const osel = select_(opsFor(fd), r.op, (v) => {
        r.op = v;
        if (v === "empty" || v === "set") { delete r.value; delete r.today; }
        else if (v === "in" || v === "not in") { delete r.today; r.value = Array.isArray(r.value) ? r.value : r.value != null && r.value !== "" ? [r.value] : []; }
        else if (Array.isArray(r.value)) r.value = r.value[0] ?? "";
        else if (isDate(fd) && !("value" in r) && !("today" in r)) r.today = "";
        commit(true);
      });
      osel.setAttribute("aria-label", `Condition ${i + 1}: how`);
      const row = el("div", { class: "dz-rule" }, fsel, osel);
      const valueBox = ruleValue(r, fd, i, commit);
      if (valueBox) row.append(valueBox);
      row.append(el("button", { type: "button", class: "subtle dz-del", "aria-label": `Remove condition ${i + 1}`,
        onclick: () => { f.rules.splice(i, 1); commit(true); } }, "×"));
      box.append(row);
    });
    box.append(el("div", { class: "dz-row" },
      el("button", { type: "button", class: "subtle", disabled: !fs.length, onclick: () => {
        const x = fs[0], ch = choicesOf(x);
        f.rules.push(ch.length ? { path: x.path, op: "==", value: ch[0][0] } : isDate(x) ? { path: x.path, op: "<=", today: "" }
          : { path: x.path, op: "==", value: "" });
        commit(true);
      } }, "+ Add a condition"),
      el("button", { type: "button", class: "link", onclick: async () => {
        const res = await draft();
        const i = doc.widget.indexOf(w), wh = res && res.widgets[i] && res.widgets[i].where;
        delete w.filter;
        if (wh) w.where = wh; else delete w.where;
        asText.add(w);
        drawPanel();
      } }, "Write it as a condition")));
    if (!f.rules.length) box.append(el("p", { class: "muted small" }, "Every one, for now."));
  }
  function ruleValue(r, fd, i, commit) {
    if (r.op === "empty" || r.op === "set") return null;
    const ch = choicesOf(fd), lab = `Condition ${i + 1}: value`;
    if (r.op === "in" || r.op === "not in") {
      if (ch.length) {
        const cur = new Set(r.value || []);
        return el("div", { class: "dz-multi", role: "group", "aria-label": lab }, ch.map(([v, l]) => el("label", { class: "radio" },
          el("input", { type: "checkbox", checked: cur.has(v), onchange: (ev) => {
            if (ev.target.checked) cur.add(v); else cur.delete(v);
            r.value = ch.map((c) => c[0]).filter((x) => cur.has(x)); commit();
          } }), l)));
      }
      return input((r.value || []).join(", "), (v) => { r.value = v.split(",").map((x) => x.trim()).filter(Boolean); commit(); },
        { "aria-label": lab, placeholder: "a, b, c" });
    }
    if (ch.length) { const s = select_(ch, r.value, (v) => { r.value = v; commit(); }); s.setAttribute("aria-label", lab); return s; }
    if (fd && fd.type === "boolean") {
      const s = select_([["true", "yes"], ["false", "no"]], String(r.value), (v) => { r.value = v === "true"; commit(); });
      s.setAttribute("aria-label", lab); return s;
    }
    if (isDate(fd)) {
      const wrap = el("div", { class: "dz-date" });
      const draw = () => {
        wrap.textContent = "";
        const hs = select_([["today", "today"], ["after", "days from today"], ["before", "days ago"], ["date", "a date"]],
          "today" in r ? (/^\+/.test(r.today || "") ? "after" : /^-/.test(r.today || "") ? "before" : "today") : "date", (v) => {
            if (v === "date") { delete r.today; r.value = new Date().toISOString().slice(0, 10); }
            else { delete r.value; const n = (/(\d+)/.exec(r.today || "") || [0, 7])[1]; r.today = v === "today" ? "" : (v === "after" ? "+" : "-") + n + "d"; }
            commit(); draw();
          });
        hs.setAttribute("aria-label", lab);
        if ("today" in r && r.today) {
          const n = (/(\d+)/.exec(r.today) || [0, 7])[1];
          wrap.append(input(n, (v) => { const k = Math.max(0, parseInt(v, 10) || 0); r.today = (r.today[0] === "-" ? "-" : "+") + k + "d"; commit(); },
            { type: "number", min: "0", max: "3650", class: "dz-n", "aria-label": `Condition ${i + 1}: how many days` }));
        } else if (!("today" in r)) {
          wrap.append(input(r.value, (v) => { r.value = v; commit(); }, { type: "date", "aria-label": `Condition ${i + 1}: date` }));
        }
        wrap.append(hs);
      };
      draw();
      return wrap;
    }
    const numeric = fd && (fd.type === "number" || fd.type === "integer");
    return input(r.value, (v) => { r.value = numeric && v !== "" && !Number.isNaN(Number(v)) ? Number(v) : v; commit(); },
      { type: numeric ? "number" : "text", "aria-label": lab });
  }

  // ---- columns
  function columnsPanel(w) {
    const cols = w.columns || [];
    const box = el("fieldset", { class: "dz-cols" }, el("legend", {}, w.kind === "board" ? "On each card" : "Columns"));
    const commit = () => { if (cols.length) w.columns = cols; else delete w.columns; changed(); drawPanel(); };
    cols.forEach((c, i) => {
      const lab = input(c.label || "", (v) => { if (v) c.label = v; else delete c.label; changed(); },
        { placeholder: labelOf(w, c.path), "aria-label": `Heading of the column ${labelOf(w, c.path)}` });
      box.append(el("div", { class: "dz-col" }, el("span", { class: "dz-cn" }, labelOf(w, c.path)), lab,
        el("button", { type: "button", class: "subtle", "aria-label": `Move ${labelOf(w, c.path)} up`, disabled: i === 0,
          onclick: () => { cols.splice(i - 1, 0, cols.splice(i, 1)[0]); commit(); } }, "↑"),
        el("button", { type: "button", class: "subtle", "aria-label": `Move ${labelOf(w, c.path)} down`, disabled: i === cols.length - 1,
          onclick: () => { cols.splice(i + 1, 0, cols.splice(i, 1)[0]); commit(); } }, "↓"),
        el("button", { type: "button", class: "subtle", "aria-label": `Remove the column ${labelOf(w, c.path)}`,
          onclick: () => { cols.splice(i, 1); commit(); } }, "×")));
    });
    const used = new Set(cols.map((c) => c.path));
    const addSel = select_(fieldsOf(w).filter((f) => !used.has(colKey(w, f))).map((f) => [colKey(w, f), f.label]), "",
      (v) => { if (v) { cols.push({ path: v }); commit(); } }, "+ Add a column");
    addSel.setAttribute("aria-label", "Add a column");
    box.append(addSel);
    if (!cols.length) box.append(el("p", { class: "muted small" }, "None chosen: the title."));
    panel.append(box);
  }

  // ---- links
  function linksPanel(w) {
    const ls = w.links || (w.links = []);
    const box = el("fieldset", { class: "dz-cols" }, el("legend", {}, "Links"));
    const list = "dz-pages";
    if (!$(list)) document.body.append(el("datalist", { id: list }, cat.pages.map((p) => el("option", { value: p.href }, p.label))));
    ls.forEach((l, i) => {
      box.append(el("div", { class: "dz-link" },
        input(l.label, (v) => { l.label = v; changed(); }, { "aria-label": `Link ${i + 1}: words`, placeholder: "Words" }),
        input(l.href, (v) => { l.href = v.trim(); changed(); }, { "aria-label": `Link ${i + 1}: where to`, placeholder: "/views/…", list }),
        el("button", { type: "button", class: "subtle", "aria-label": `Remove link ${i + 1}`, onclick: () => { ls.splice(i, 1); changed(); drawPanel(); } }, "×")));
    });
    const pick = select_(cat.pages.map((p, i) => [String(i), p.label]), "", (v) => {
      if (v !== "") { ls.push({ ...cat.pages[+v] }); changed(); drawPanel(); }
    }, "+ Add a page of the hub");
    pick.setAttribute("aria-label", "Add a page of the hub");
    box.append(pick, el("button", { type: "button", class: "subtle", onclick: () => { ls.push({ label: "", href: "" }); drawPanel(); } }, "+ Another address"));
    panel.append(box);
    placePanel(w);
  }

  // ---- drafts: the text, the checks and the preview, from the hub
  function clean() { return JSON.parse(JSON.stringify(doc)); }
  async function post(body) {
    const r = await fetch("/api/views/draft", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json();
  }
  async function draft() {
    const mine = ++seq;
    status.textContent = "Checking…";
    try {
      const res = await post({ doc: clean() });
      if (mine !== seq) return res;
      last = res;
      show(res);
      return res;
    } catch (err) {
      status.textContent = "Could not reach the hub to check the view.";
    }
  }
  function show(res) {
    status.textContent = "";
    const ps = res.problems || [];
    if (ps.length && !touched) status.append("Give the view a title and add widgets; the hub checks it as you go.");
    else if (!ps.length) status.append(el("span", { class: "loz done" }, "ready"), " The hub accepts this.");
    else status.append(el("span", { class: "loz bad" }, ps.length + " problem" + (ps.length === 1 ? "" : "s")),
      el("ul", { class: "ed-problems" }, ps.map((p) => el("li", {}, p))));
    (res.widgets || []).forEach((p, i) => {
      const w = doc.widget[i];
      if (!w) return;
      const v = views.get(keyOf(w));
      if (!v) return;
      const body = v.querySelector(".dz-body"), total = v.querySelector(".dz-total");
      if (p.problems) {
        body.textContent = "";
        body.append(el("ul", { class: "dz-wp" }, p.problems.map((x) => el("li", {}, x.replace(/^widget [^:]*: /, "")))));
        total.textContent = ""; v.classList.add("bad");
      } else {
        body.innerHTML = p.html;  // the server's HTML, escaped there
        total.textContent = w.kind === "links" ? "" : String(p.total);
        v.classList.remove("bad");
      }
    });
  }
  function changed(soft) {
    touched = true;
    clearTimeout(timer);
    timer = setTimeout(draft, soft ? 150 : 400);
  }
  function say(t) { const s = $("dz-say"); if (s) s.textContent = t; }
  root.append(el("div", { id: "dz-say", class: "visually-hidden", "aria-live": "polite" }));

  // ---- canvas or code
  const modeBtns = root.querySelectorAll(".dz-mode button");
  async function setMode(m) {
    if (m === mode) return;
    if (m === "code") {
      const res = await draft();
      if (!res) return;
      toml.value = res.source || "";
    } else {
      const res = await post({ source: toml.value });
      if (!res.doc) { show(res); return; }
      doc = res.doc; doc.widget = doc.widget || [];
      for (const [, v] of views) v.remove();
      views.clear(); sel = null; idTouched = true;
      last = res;
    }
    mode = m;
    main.hidden = m === "code"; code.hidden = m !== "code";
    modeBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === m)));
    if (m === "canvas") { drawCanvas(); drawPanel(); show(last); }
    else toml.focus();
  }
  modeBtns.forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  toml.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => { const res = await post({ source: toml.value }); status.textContent = ""; show({ ...res, widgets: [] }); }, 500);
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!form.reportValidity()) return;
    let source = toml.value;
    if (mode === "canvas") { const res = await draft(); if (!res) return; source = res.source; }
    form.elements.source.value = source;
    HTMLFormElement.prototype.submit.call(form);
  });

  drawCanvas();
  drawPanel();
  const deep = /^#w(\d+)$/.exec(location.hash);  // #w2: open with the second widget chosen
  if (deep && doc.widget[+deep[1] - 1]) select(doc.widget[+deep[1] - 1], true);
  if (boot.problems && boot.problems.length) touched = true, show({ problems: boot.problems, widgets: [] });
  draft();
}

document.addEventListener("DOMContentLoaded", () => {
  const ed = document.getElementById("dash-editor");
  if (ed) dashEditor(ed);
});
})();
"""

JS = JS + DASH_JS

DASH_CSS = r"""
/* ---- dashboards: a view's widgets on a 12-column grid (/views/<id>) and the editor that places them */
.dash-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));grid-auto-rows:minmax(56px,auto);gap:12px;
align-items:stretch;margin:0 0 16px}
.dash-cell{min-width:0;background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:12px 16px;overflow:auto}
.dash-cell>.section{margin:0}.dash-cell>.section.card{box-shadow:none;padding:0;background:transparent}
.dash-cell h2{font-size:14px;line-height:20px;margin:0 0 8px}
.dash-cell .board{overflow-x:auto}
ul.dash-links,ul.dash-list{list-style:none;padding:0;margin:0}
ul.dash-links li,ul.dash-list li{padding:6px 0;border-top:1px solid var(--border)}
ul.dash-links li:first-child,ul.dash-list li:first-child{border-top:0}
ul.dash-links a{font-weight:500}
@media (max-width:720px){.dash-grid{display:flex;flex-direction:column}.dash-cell{grid-column:auto!important;grid-row:auto!important}}

.dz-bar{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;margin:0 0 12px}
.dz-grow{flex:1}
.dz-mode{display:inline-flex;border-radius:3px;box-shadow:inset 0 0 0 1px var(--control-border);overflow:hidden}
.dz-mode button{border-radius:0;box-shadow:none;background:transparent}
.dz-mode button[aria-pressed=true]{background:var(--selected-bg);color:var(--selected-fg)}
#dz-status{font-size:13px;line-height:20px;max-width:640px;color:var(--subtle)}
#dz-status .ed-problems{margin:4px 0 0;padding-left:18px;color:var(--danger-fg)}
.dz-main{display:grid;grid-template-columns:200px minmax(0,1fr) 320px;gap:16px;align-items:start}
.dz-main[hidden],.dz-code[hidden]{display:none}
.dz-palette,.dz-panel{background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:12px;
position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto}
.dz-palette h2,.dz-panel h2{font-size:14px;line-height:20px;margin:0 0 8px}
.dz-kinds{display:flex;flex-direction:column;gap:6px}
button.dz-kind{height:auto;justify-content:flex-start;text-align:left;white-space:normal;padding:8px;gap:10px;cursor:grab;
background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)}
button.dz-kind:hover{background:var(--neutral-hover)}
.dz-glyph{flex:0 0 32px;height:32px;border-radius:3px;display:grid;place-items:center;background:var(--selected-bg);
color:var(--selected-fg);font-size:12px;font-weight:700}
.dz-kl{display:flex;flex-direction:column;min-width:0}.dz-kl b{color:var(--fg);font-size:13px}
.dz-kl small{font-weight:400;font-size:11px;line-height:14px;color:var(--subtlest)}
.dz-stage{position:relative;min-width:0}
.dz-canvas{position:relative;min-height:336px;border-radius:3px;
background-color:var(--sunken);
background-image:linear-gradient(to right,var(--border) 1px,transparent 1px),linear-gradient(to bottom,var(--border) 1px,transparent 1px);
background-size:var(--colw,60px) var(--rowh,68px);background-position:-6px -6px;outline:1px dashed var(--border-bold);outline-offset:0}
.dz-canvas.drop{outline:2px solid var(--focus)}
.dz-empty{position:absolute;top:40px;left:50%;transform:translateX(-50%);width:min(420px,calc(100% - 32px));text-align:center;
color:var(--subtle);pointer-events:none;margin:0;padding:12px 16px;background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised)}
.dz-w{position:absolute;display:flex;flex-direction:column;background:var(--raised);border-radius:3px;
box-shadow:var(--shadow-raised);overflow:hidden;transition:left .12s,top .12s,width .12s,height .12s}
.dz-w:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.dz-w.on{box-shadow:0 0 0 2px var(--brand),var(--shadow-raised)}
.dz-w.bad{box-shadow:0 0 0 2px var(--danger),var(--shadow-raised)}
.dz-w.lifted{transition:none;z-index:5;box-shadow:var(--shadow-overlay);opacity:.92}
.dz-ghost{position:absolute;border-radius:3px;background:var(--selected-bg);outline:2px dashed var(--brand);z-index:1;pointer-events:none}
.dz-head{display:flex;align-items:center;gap:6px;padding:4px 4px 4px 8px;border-bottom:1px solid var(--border);
cursor:move;touch-action:none;user-select:none;min-height:32px}
.dz-grip{color:var(--subtlest);font-size:14px}
.dz-kindl{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtlest);white-space:nowrap}
.dz-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
.dz-total:empty{display:none}
button.dz-x{height:24px;width:24px;padding:0;font-size:16px}
.dz-body{flex:1;min-height:0;overflow:hidden;padding:8px 12px;font-size:13px}
.dz-body .section{margin:0}.dz-body .section>h2:first-child{display:none}
.dz-body .section.card{box-shadow:none;padding:0;background:transparent}
.dz-body .stat{margin:0;font-size:32px;line-height:40px;font-weight:600}
.dz-body table{font-size:12px}.dz-body .board .col{min-width:120px}
.dz-wp{margin:0;padding-left:16px;color:var(--danger-fg);font-size:12px}
.dz-resize{position:absolute;right:0;bottom:0;width:16px;height:16px;cursor:nwse-resize;touch-action:none;
background:linear-gradient(135deg,transparent 50%,var(--border-bold) 50%,var(--border-bold) 60%,transparent 60%,transparent 70%,var(--border-bold) 70%,var(--border-bold) 80%,transparent 80%)}
.dz-panel .dz-field{margin:0 0 8px}.dz-panel label{margin-top:8px}
.dz-panel .small,.dz-palette .small{font-size:12px;line-height:16px}
.dz-ph{display:flex;align-items:center;gap:4px;flex-wrap:wrap}.dz-ph h2{flex:1;margin:0}
.dz-ph button{height:28px;padding:0 8px;font-size:13px}
.dz-panel fieldset{border:0;border-top:1px solid var(--border);margin:12px 0 0;padding:8px 0 0;min-width:0}
.dz-panel legend{font-size:12px;font-weight:600;color:var(--subtle);padding:0 4px 0 0}
.dz-rule{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) 28px;gap:4px;align-items:start;margin:0 0 8px;
padding:6px;background:var(--sunken);border-radius:3px}
.dz-rule>:nth-child(3):not(button){grid-column:1/3}
.dz-rule select,.dz-rule input{margin:0}
.dz-rule .dz-del{grid-column:3;grid-row:1}
.dz-multi{grid-column:1/3;display:flex;flex-wrap:wrap}.dz-multi label.radio{margin:2px 10px 2px 0;font-size:13px}
.dz-date{display:flex;gap:4px}.dz-date .dz-n{width:72px;flex:0 0 72px}
.dz-row{display:flex;gap:4px 8px;align-items:center;flex-wrap:wrap}.dz-row select{flex:1 1 100%;min-width:0}
.dz-col,.dz-link{display:flex;gap:4px;align-items:center;margin:0 0 4px}
.dz-col .dz-cn{flex:0 0 35%;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dz-col input,.dz-link input{margin:0;flex:1;min-width:0}
.dz-col button,.dz-link button{height:28px;width:28px;padding:0}
.dz-quad{display:grid;grid-template-columns:1fr 1fr;gap:0 8px}
.dz-chips label.radio{font-size:13px;margin:2px 12px 2px 0}
.dz-check{margin-top:12px}
.dz-code textarea{min-height:28em}
.dz-foot{margin-top:16px}
@media (max-width:1100px){.dz-main{grid-template-columns:minmax(0,1fr) 300px}
  .dz-palette{grid-column:1/-1;position:static;max-height:none}.dz-kinds{flex-direction:row;flex-wrap:wrap}
  button.dz-kind{flex:1 1 150px}}
@media (max-width:760px){.dz-main{grid-template-columns:minmax(0,1fr)}.dz-panel{position:static;max-height:none}
  .dz-kl small{display:none}button.dz-kind{flex:1 1 96px;padding:6px}.dz-glyph{flex-basis:24px;height:24px}
  .dz-kinds{gap:4px}}
"""

CSS = CSS + DASH_CSS

# a record's work, on its own page: where it is beside the title, what can be done now under it
RECWORK_CSS = r"""
.rec-status{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;min-height:32px}
.rs-item{display:inline-flex;gap:6px;align-items:center}
.rec-work{border:1px solid var(--line);border-radius:6px;background:var(--sunken);padding:12px 16px;margin:0 0 16px}
.rec-work>*+*{margin-top:12px}
.rw-moves{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.rw-moves form.inline{margin:0}
.rw-step{border-top:1px solid var(--line);padding-top:12px}.rw-step>h3{margin:0 0 8px}
.rw-verbs{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}
.rw-not>summary,.cancel-work>summary{cursor:pointer;color:var(--subtle);font-size:13px}
.rw-not ul{margin:8px 0 0}
.sign-move{border-left:3px solid var(--line-2);padding-left:12px}
.wp-lifecycle{margin:0 0 12px}
ol.timeline{list-style:none;padding:0;margin:0}
ol.timeline li{padding:6px 0;border-bottom:1px solid var(--line)}
.tl-at{display:inline-block;min-width:9em;font-size:13px}
.tl-versions{margin-top:16px}
.filters.when{display:inline-flex;gap:4px;margin-left:8px}.filters.when .btn.selected{background:var(--selected-bg);color:var(--selected-fg)}
.rw-activity{border-top:1px solid var(--line);padding-top:12px}
.rw-activity>details>summary{cursor:pointer;list-style-position:outside}
.rw-activity .ra-h{display:inline;margin:0;font-size:15px}
.rw-activity .ra-then{margin:12px 0 4px;font-size:14px}
ul.ra-list{margin:8px 0 0}ul.ra-list>li{padding:2px 0}
ul.ra-started{margin:4px 0 0 20px}ul.ra-started>li{padding:2px 0}
.ra-mark{display:inline-block;width:1em;text-align:center;color:var(--subtle)}
.ra-word.bad{font-weight:600}.ra-failed .ra-mark{color:var(--danger-fg)}
.ra-live{margin:8px 0 0}
.tl-ran{margin:4px 0 0 9em}.tl-ran>summary{cursor:pointer;color:var(--subtle);font-size:13px}
@media (max-width:640px){.tl-ran{margin-left:0}}
"""

CSS = CSS + RECWORK_CSS

STRUCTURE_CSS = """
.st-layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:24px;align-items:start}
@media (max-width:1000px){.st-layout{grid-template-columns:1fr}}
.st-canvas{overflow:auto;border:1px solid var(--border);border-radius:6px;background:var(--sunken);padding:8px}
.st-legend{display:flex;gap:8px 14px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--subtle);margin:0 0 6px 4px}
.st-key{display:inline-block;width:22px;height:0;border-top:2px solid var(--border-bold);vertical-align:middle}
.st-key.hand{border-top-color:var(--brand)}.st-key.field{border-top:2px dashed var(--subtlest)}
.st-svg{display:block;font:13px/1 inherit;touch-action:none;user-select:none}
.st-svg .st-node rect,.st-svg .st-root rect{fill:var(--surface);stroke:var(--border-bold);stroke-width:1}
.st-svg .st-root rect{fill:transparent;stroke-dasharray:4 3}.st-svg .st-root text{fill:var(--subtle)}
.st-svg .st-node text{fill:var(--fg)}.st-svg .st-node{cursor:grab}
.st-svg .st-node:hover rect,.st-svg .st-node:focus rect{stroke:var(--brand);stroke-width:2}
.st-svg .st-node:focus{outline:none}.st-svg .st-node:focus-visible rect{stroke:var(--focus);stroke-width:3}
.st-svg .st-node.selected rect{fill:var(--selected-bg);stroke:var(--selected-fg);stroke-width:2}
.st-svg .st-node.proposed rect{stroke:var(--warning);stroke-dasharray:5 3}
.st-svg .st-badge{fill:var(--warning-fg);font-size:11px}
.st-svg .drop rect{stroke:var(--success);stroke-width:3;fill:var(--success-bg)}
.st-svg .dragging{opacity:.75;cursor:grabbing}.st-svg.is-dragging .st-arcs{opacity:.25}
.st-svg .st-part{fill:none;stroke:var(--border-bold);stroke-width:1.5}
.st-svg .st-arc path{fill:none;stroke-width:1.5}.st-svg .st-arc text{font-size:11px}
.st-svg .st-arc.hand path{stroke:var(--brand)}.st-svg .st-arc.hand text{fill:var(--brand)}
.st-svg .st-arc.field{display:none}.st-svg.show-fields .st-arc.field{display:inline}
.st-svg .st-arc.field path{stroke:var(--subtlest);stroke-dasharray:4 3}
.st-svg .st-arc.hl path{stroke-width:3}.st-svg .st-arc.field.hl path{stroke:var(--subtle)}
.st-svg #st-arrow path{fill:var(--subtle)}
.st-side h2{margin-top:0}.st-side form.card{margin:12px 0}.st-links li{padding:4px 0}
.st-links form.inline{display:inline;margin-left:6px}
"""
CSS = CSS + STRUCTURE_CSS

STRUCTURE_JS = r"""
// The record structure (/define/structure): drag a type onto another to propose making it a part of that one, or
// onto "Top level"; hovering a type lights its links; a tick shows what reference fields point at too.
(function () {
  const svg = document.querySelector(".st-svg");
  if (!svg) return;
  const fields = document.getElementById("st-fields");
  if (fields) fields.addEventListener("change", () => svg.classList.toggle("show-fields", fields.checked));
  const light = (t, on) => svg.querySelectorAll(".st-arc").forEach((a) => {
    if (a.dataset.from === t || a.dataset.to === t) a.classList.toggle("hl", on);
  });
  svg.querySelectorAll(".st-node").forEach((n) => {
    n.addEventListener("pointerenter", () => light(n.dataset.type, true));
    n.addEventListener("pointerleave", () => light(n.dataset.type, false));
    n.addEventListener("focus", () => light(n.dataset.type, true));
    n.addEventListener("blur", () => light(n.dataset.type, false));
  });
  const sel = svg.querySelector(".st-node.selected");
  if (sel) light(sel.dataset.type, true);
  const dlg = document.getElementById("st-move");
  if (!dlg || !dlg.showModal) return;
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  let drag = null;
  const scale = () => { const r = svg.getBoundingClientRect(); return svg.viewBox.baseVal.width / r.width; };
  svg.addEventListener("pointerdown", (ev) => {
    const n = ev.target.closest(".st-node");
    if (!n || ev.button !== 0) return;
    drag = { n, x: ev.clientX, y: ev.clientY, moved: false, over: null };
  });
  window.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 6) return;
    if (!drag.moved) { drag.moved = true; drag.n.classList.add("dragging"); svg.classList.add("is-dragging"); }
    const k = scale();
    drag.n.setAttribute("transform", `translate(${dx * k},${dy * k})`);
    drag.n.style.pointerEvents = "none";
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    const over = under && under.closest(".st-node, .st-root");
    if (drag.over && drag.over !== over) drag.over.classList.remove("drop");
    drag.over = over && over !== drag.n ? over : null;
    if (drag.over) drag.over.classList.add("drop");
  });
  window.addEventListener("pointerup", () => {
    if (!drag) return;
    const d = drag; drag = null;
    if (!d.moved) return;
    d.n.removeAttribute("transform"); d.n.style.pointerEvents = ""; d.n.classList.remove("dragging");
    svg.classList.remove("is-dragging");
    const stop = (ev) => { ev.preventDefault(); d.n.removeEventListener("click", stop, true); };
    d.n.addEventListener("click", stop, true);  // a drag is not a click on the type
    setTimeout(() => d.n.removeEventListener("click", stop, true), 0);
    if (!d.over) return;
    d.over.classList.remove("drop");
    const owner = d.over.dataset.type || "";
    const f = dlg.querySelector("form");
    f.elements.type.value = d.n.dataset.type;
    f.elements.owner.value = owner;
    dlg.querySelector("#st-move-what").textContent = owner
      ? `Make ${d.n.dataset.label} part of ${d.over.dataset.label}? Each will belong to one, and be shown on its page.`
      : `Make ${d.n.dataset.label} stand alone, part of nothing?`;
    dlg.showModal();
    f.elements.note.focus();
  });
})();
"""
JS = JS + STRUCTURE_JS

