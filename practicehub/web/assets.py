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
--fg:#B6C2CF;--subtle:#9FADBC;--subtlest:#8C9BAB;--border:#A6C5E229;--border-bold:#738496;
--neutral:#A1BDD914;--neutral-hover:#A6C5E229;--neutral-pressed:#BFDBF847;
--disabled-bg:#BCD6F00A;--disabled-fg:#BFDBF847;
--brand:#579DFF;--brand-hover:#85B8FF;--brand-pressed:#CCE0FF;--brand-fg:#1D2125;
--selected-bg:#1C2B41;--selected-fg:#579DFF;--link:#579DFF;--link-hover:#85B8FF;
--input-bg:#22272B;--input-hover:#282E33;--input-border:#A6C5E229;
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
--shadow-raised:0 0 0 1px #00000000,0 1px 1px #03040480,0 0 1px #03040480;
--shadow-overlay:0 0 0 1px #BCD6F00A,0 8px 12px #0304045C,0 0 1px 1px #03040480;
--backdrop:#10121499;
"""

CSS = (r"""
:root{color-scheme:light;
--bg:#FFFFFF;--surface:#FFFFFF;--raised:#FFFFFF;--raised-hover:#F1F2F4;--overlay:#FFFFFF;--sunken:#F7F8F9;
--fg:#172B4D;--subtle:#44546F;--subtlest:#626F86;--border:#091E4224;--border-bold:#758195;
--neutral:#091E420F;--neutral-hover:#091E4224;--neutral-pressed:#091E424F;
--disabled-bg:#091E4208;--disabled-fg:#091E424F;
--brand:#0C66E4;--brand-hover:#0055CC;--brand-pressed:#09326C;--brand-fg:#FFFFFF;
--selected-bg:#E9F2FF;--selected-fg:#0C66E4;--link:#0C66E4;--link-hover:#0055CC;
--input-bg:#F7F8F9;--input-hover:#FFFFFF;--input-border:#091E4224;
--danger:#C9372C;--danger-hover:#AE2E24;--danger-on:#FFFFFF;--danger-bg:#FFECEB;--danger-fg:#AE2E24;--danger-icon:#C9372C;
--success:#1F845A;--success-bg:#DCFFF1;--success-fg:#216E4E;--success-icon:#22A06B;
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
--shadow-raised:0 1px 1px #091E4240,0 0 1px #091E424F;
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
:focus-visible{outline:2px solid #388BFF;outline-offset:2px}
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
.pagehead h1{flex:1;min-width:min(240px,100%)}
.pagehead .actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}

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
.note{font-size:14px;color:var(--danger-fg)}
nav.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:2px solid var(--border);margin:0 0 16px}nav.tabs a{padding:6px 10px;color:var(--subtle);text-decoration:none;margin-bottom:-2px;border-bottom:2px solid transparent}nav.tabs a:hover{color:var(--fg)}nav.tabs a[aria-current]{color:var(--selected-fg);border-bottom-color:currentColor;font-weight:600}.issue-side h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle);margin:16px 0 4px}.sign{font-size:14px}

/* ---- tables (wrap wide ones in <div class="tablewrap">) */
.tablewrap{overflow-x:auto;max-width:100%;margin:0 0 16px}
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
/* a board: a card can be dragged to a column it may move to */
.tile[draggable=true]{cursor:grab}.tile.dragging{opacity:.5}
.board .col.drop-ok{outline:2px dashed var(--brand);outline-offset:-2px}
.board .col.drop-over{background:var(--selected,var(--neutral))}
.board .col.drop-no{opacity:.6}
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
dialog.form-modal .fm-close:focus-visible{outline:2px solid #388BFF;outline-offset:2px}
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
table.parts{table-layout:fixed;width:100%}table.parts td.t>a{font-weight:500}
table.parts th:nth-child(2){width:150px}table.parts th:nth-child(3){width:130px}table.parts th:nth-child(4){width:110px}
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
input:focus,select:focus,textarea:focus{background:var(--input-hover);border-color:#388BFF;box-shadow:inset 0 0 0 1px #388BFF}
/* the border is the focus ring for text controls; a transparent outline keeps it visible in forced-colours mode */
input:not([type=checkbox]):not([type=radio]):focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid transparent}
input:disabled,select:disabled,textarea:disabled{background:var(--disabled-bg);color:var(--disabled-fg);cursor:not-allowed}
input[aria-invalid=true],select[aria-invalid=true],textarea[aria-invalid=true]{border-color:var(--danger)}
input[type=checkbox],input[type=radio]{display:inline-block;width:16px;height:16px;min-height:0;margin:0 8px 0 0;padding:0;
vertical-align:middle;accent-color:var(--brand);box-shadow:none}
input[type=hidden]{display:none}
label.radio{font-weight:400;color:var(--fg);display:inline-flex;align-items:center;margin:4px 16px 4px 0;font-size:14px;line-height:20px}
textarea.code{font:13px/20px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
fieldset.choices{border:0;margin:12px 0 0;padding:0;min-width:0}
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
.filters input{width:auto;flex:0 1 240px;margin:0}
.chip{display:inline-flex;align-items:center;gap:4px;height:32px;padding:0 12px;border-radius:3px;font-weight:500;
color:var(--subtle);background:var(--neutral)}
.chip:hover{background:var(--neutral-hover);color:var(--subtle);text-decoration:none}
.chip.on,.chip[aria-current],.chip[aria-pressed=true]{background:var(--selected-bg);color:var(--selected-fg)}
.filters .gap{width:8px}.filters .lbl{font-size:12px;line-height:16px;color:var(--subtle);font-weight:600}

/* ---- board */
.stat{font-size:32px;line-height:40px;font-weight:600;margin:0}
table.chart th{width:30%;font-weight:400}.bar{display:inline-block;height:12px;border-radius:2px;background:var(--brand);vertical-align:middle}
.board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(272px,1fr);gap:8px;overflow-x:auto;padding-bottom:8px}
.col{background:var(--sunken);border-radius:3px;padding:8px;min-height:200px}
.col>header{display:flex;align-items:center;gap:8px;padding:4px 4px 12px;font-size:12px;line-height:16px;font-weight:600;
color:var(--subtle);text-transform:uppercase}.col>header .n{font-weight:600}
.lane{margin:16px 0 8px;font-weight:600;display:flex;align-items:center;gap:8px}
.tile{display:block;background:var(--raised);border-radius:3px;box-shadow:var(--shadow-raised);padding:12px;margin-bottom:8px;color:var(--fg)}
.tile:hover{background:var(--raised-hover);color:var(--fg);text-decoration:none}
.tile .t{font-weight:400;margin-bottom:8px}.tile .about{font-size:12px;line-height:16px;color:var(--subtlest);margin:-4px 0 8px}
.tile .foot{display:flex;align-items:center;gap:8px}.tile .foot .av{margin-left:auto}
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
.fl-node:focus-visible{outline:2px solid #388BFF}
.fl-node .ai{fill:var(--discovery-bg)}.fl-node text.ai-t{fill:var(--discovery-fg);font-size:9px;font-weight:800}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;line-height:16px;color:var(--subtle);margin:8px 4px}
.legend span{display:inline-flex;align-items:center;gap:4px}

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
@media (max-width:480px){
  main table:not(.kv){display:block;overflow-x:auto;white-space:nowrap}
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
    if (s.when) path.append(sv("title", {}, "only when " + s.when));
    svg.append(path);
  }
  for (const s of steps) {
    const { x, y } = pos.get(s.id), st = states[s.id];
    const clickable = !!(opts.onSelect || links[s.id]);
    const g = sv("g", { class: `fl-node k-${s.kind} ${st ? "st-" + st : ""} ${opts.selected === s.id ? "sel" : ""} ${clickable ? "click" : ""}`,
      tabindex: clickable ? "0" : "-1" });
    g.append(sv("title", {}, `${s.title} (${s.kind}${s.role ? ", " + s.role : ""})${s.when ? " — only when " + s.when : ""}`));
    g.append(sv("rect", { x, y, width: W, height: H, rx: 7, class: "box" }));
    g.append(sv("rect", { x, y: y + 6, width: 4, height: H - 12, rx: 2, class: "bar" }));
    const kind = (s.kind === "gate" ? "decision" : s.kind).toUpperCase();
    g.append(sv("text", { x: x + 14, y: y + 18, class: "k" }, clip(kind + " · " + (s.role || s.action || ""), 30)));
    g.append(sv("text", { x: x + 14, y: y + 36 }, clip(s.title || s.id, 26)));
    g.append(sv("text", { x: x + 14, y: y + 52, class: "s" }, st ? st : (s.when ? "if " + clip(s.when, 24) : s.id)));
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
window.toToml = (wf) => toToml(wf);

// ------------------------------------------------------------------ TOML out (the server parses it back)
const BARE = /^[A-Za-z0-9_-]+$/;
const key = (k) => (BARE.test(k) ? k : JSON.stringify(k));
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const Q3 = '"'.repeat(3);
function str(s) {
  if (s.includes("\n") && !s.includes(Q3) && !s.endsWith('"') && !/[\x00-\x08\x0b-\x1f\x7f]/.test(s.replace(/\r\n/g, "\n")))
    return Q3 + "\n" + s.replace(/\r\n/g, "\n").replace(/\\/g, "\\\\") + Q3;
  return JSON.stringify(s);
}
function val(v) {
  if (typeof v === "string") return str(v);
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "0";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return "[" + v.map(val).join(", ") + "]";
  if (isObj(v)) return "{ " + Object.entries(v).filter(([, x]) => x !== null && x !== undefined)
    .map(([k, x]) => key(k) + " = " + val(x)).join(", ") + " }";
  return '""';
}
const DROP_FALSE = new Set(["persons_only", "required"]);
function clean(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (k.startsWith("_") || v === null || v === undefined || v === "") continue;
    if (Array.isArray(v) && !v.length) continue;
    if (isObj(v) && !Object.keys(v).length) continue;
    if (v === false && DROP_FALSE.has(k)) continue;
    out[k] = v;
  }
  return out;
}
function scalarLines(obj) {
  return Object.entries(obj).filter(([, v]) => !(isObj(v) || (Array.isArray(v) && v.length && v.every(isObj))))
    .map(([k, v]) => `${key(k)} = ${val(v)}`);
}
function toToml(wf) {
  wf = clean(wf);
  const out = [];
  const head = ["id", "title", "record", "summary"];
  const top = {};
  for (const k of head) if (k in wf) top[k] = wf[k];
  for (const [k, v] of Object.entries(wf)) if (!head.includes(k)) top[k] = v;
  out.push(...scalarLines(top));
  for (const [k, v] of Object.entries(top)) if (isObj(v)) {
    out.push("", `[${key(k)}]`, ...scalarLines(clean(v)));
  }
  for (const [k, v] of Object.entries(top)) if (Array.isArray(v) && v.length && v.every(isObj)) {
    for (const item of v) {
      const it = clean(item);
      out.push("", `[[${key(k)}]]`, ...scalarLines(it));
      for (const [k2, v2] of Object.entries(it)) if (isObj(v2)) out.push(`[${key(k)}.${key(k2)}]`, ...scalarLines(clean(v2)),
        ...Object.entries(clean(v2)).filter(([, x]) => isObj(x) || (Array.isArray(x) && x.length && x.every(isObj)))
          .map(([k3, x]) => `${key(k3)} = ${val(x)}`));
      for (const [k2, v2] of Object.entries(it)) if (Array.isArray(v2) && v2.length && v2.every(isObj))
        for (const sub of v2) out.push(`[[${key(k)}.${key(k2)}]]`, ...scalarLines(clean(sub)),
          ...Object.entries(clean(sub)).filter(([, x]) => isObj(x)).map(([k3, x]) => `${key(k3)} = ${val(x)}`));
    }
  }
  return out.join("\n").replace(/^\n+/, "") + "\n";
}

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
      const j = await r.json();
      s.textContent = "";
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
  if (t.value.trim()) check();
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
  const BLOCK = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "BLOCKQUOTE", "PRE", "HR", "TABLE"]);

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
  const cells = (line) => line.trim().replace(/^\|+|\|+$/g, "").split("|").map((c) => c.trim());

  // Markdown to HTML: the same subset, read the same way, as practicehub/markup.py.
  function toHtml(text, titles, depth) {
    titles = titles || {}; depth = depth || 0;
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n"), out = [];
    let para = [], i = 0, m;
    const flush = () => { if (para.length) { out.push("<p>" + para.map((p) => inline(p, titles)).join("<br>") + "</p>"); para = []; } };
    while (i < lines.length) {
      const s = lines[i].trim();
      if (s.startsWith("```")) {
        flush(); const code = []; i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
        out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); i++; continue;
      }
      if (!s) { flush(); i++; continue; }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(s)) && m[2].replace(/[\s#]+$/, "")) {
        flush(); const n = Math.max(2, m[1].length);
        out.push(`<h${n}>${inline(m[2].replace(/\s+$/, "").replace(/#+$/, "").replace(/\s+$/, ""), titles)}</h${n}>`); i++; continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(s)) { flush(); out.push("<hr>"); i++; continue; }
      if (s.startsWith(">")) {
        flush(); const q = [];
        while (i < lines.length && lines[i].trim().startsWith(">")) q.push(lines[i++].trim().slice(1).replace(/^\s+/, ""));
        out.push("<blockquote>" + (depth < 4 ? toHtml(q.join("\n"), titles, depth + 1)
          : "<p>" + q.filter(Boolean).map((x) => inline(x, titles)).join("<br>") + "</p>") + "</blockquote>");
        continue;
      }
      if (s.startsWith("|") && i + 1 < lines.length && SEP.test(lines[i + 1].trim())) {
        flush(); const head = cells(s); i += 2; const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
        out.push("<table><thead><tr>" + head.map((c) => `<th>${inline(c, titles)}</th>`).join("") + "</tr></thead><tbody>"
          + rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c, titles)}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
        continue;
      }
      if ((m = /^([-*+]|\d{1,3}\.)\s+/.exec(s))) {
        flush(); const ordered = /\d/.test(m[1][0]), items = [];
        while (i < lines.length) {
          const m2 = /^\s*([-*+]|\d{1,3}\.)\s+(.*)$/.exec(lines[i]);
          if (m2 && /\d/.test(m2[1][0]) === ordered) items.push(m2[2]);
          else if (/^( {2}|\t)/.test(lines[i]) && lines[i].trim() && items.length) items[items.length - 1] += " " + lines[i].trim();
          else break;
          i++;
        }
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag}>` + items.map((x) => `<li>${inline(x, titles)}</li>`).join("") + `</${tag}>`);
        continue;
      }
      para.push(s); i++;
    }
    flush();
    return out.join("");
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
        own.querySelectorAll("ul,ol").forEach((x) => x.remove());
        const x = inlineMd(own).replace(/\s*\n\s*/g, " ").trim();
        if (x) lines.push((ordered ? (n++) + ". " : "- ") + x);
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
      else if (t === "BLOCKQUOTE") {
        const x = hasBlock(c) ? toMd(c) : inlineMd(c).trim();
        if (x) out.push(x.split("\n").map((l) => (l ? "> " + l : ">")).join("\n"));
      } else if (t === "PRE") out.push("```\n" + c.textContent.replace(/\n$/, "").replace(/```/g, "ˋˋˋ") + "\n```");
      else if (t === "HR") out.push("---");
      else if (t === "TABLE") { const x = tableMd(c); if (x) out.push(x); }
    }
    flushLoose();
    return out.join("\n\n");
  }
  return { toHtml, toMd, esc };
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
function editor(root) {
  const boot = JSON.parse(document.getElementById("wf-boot").textContent);
  const cat = boot.catalog;
  let wf = boot.data || { id: "", title: "", steps: [] };
  wf.steps = wf.steps || [];
  let selected = null, tab = boot.data ? "visual" : "toml", lastSource = null, timer = null, checking = 0;

  const svg = root.querySelector("#wf-canvas"), panel = root.querySelector("#wf-panel");
  const tomlBox = root.querySelector("#wf-toml"), status = root.querySelector("#wf-status");
  if (!status.hasAttribute("aria-live")) status.setAttribute("aria-live", "polite");
  let dirty = false;  // unsaved changes: the page asks before it is left
  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });
  const form = root.querySelector("#wf-propose"), sourceIn = form.querySelector("input[name=source]");

  const agentsMay = (s) => s.kind !== "automation" && !s.persons_only && !(s.kind === "gate" && s.signer === "person");
  const stepById = (id) => wf.steps.find((s) => s.id === id);

  function redraw() {
    drawFlow(svg, { steps: wf.steps.map((s) => ({ id: s.id, title: s.title || s.id, kind: s.kind || "task",
      role: s.role, action: s.block ? "block " + s.block : s.action, after: s.after || [], when: s.when, agents_may: agentsMay(s) })) },
      { selected, onSelect: (id) => { selected = id; redraw(); renderPanel(); } });
  }
  function changed(rebuild) {
    dirty = true;
    redraw();
    if (rebuild) renderPanel();
    schedule();
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(check, 500); }
  function currentSource() { return tab === "toml" ? tomlBox.value : toToml(wf); }

  async function check() {
    const source = currentSource();
    if (source === lastSource) return;
    lastSource = source;
    const mine = ++checking;
    status.textContent = "Checking…";
    try {
      const r = await fetch("/api/workflows/check", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source }) });
      const res = await r.json();
      if (mine !== checking) return;
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
    return el("label", {}, label, ctl, o.help ? el("small", {}, o.help) : null);
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
  function chooseBlock(s, bid) {
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
      if (s.kind === "automation") { s.action = "message.send"; s.params = {}; }
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
      ? "A plain value is written into the agent's prompt. {{input.x}} or {{step.field}} is read from the run and stays data: the step must also read it (What it reads)."
      : "A plain value, or {{run.record}}, {{input.x}}, {{step.field}} read from the run.";
    return el("fieldset", {}, el("legend", {}, "Block parameters"), ...rows, el("small", { class: "muted" }, tip),
      el("p", {}, el("a", { href: "/blocks/" + b.id, target: "_blank" }, "Open the block"),
        el("span", { class: "muted" }, ` · returns ${b.outputs.join(", ") || "the fields of " + b.capability}` +
          (b.kind === "task" ? ` · agents need ${b.capability} in their profile` : ""),
          b.active ? "" : " · not approved yet: the workflow cannot be approved until it is")));
  }

  // ---- step operations
  function renameStep(s, nid) {
    nid = nid.trim();
    const old = s.id;
    if (!nid || nid === old) return;
    s.id = nid;
    const re = new RegExp("\\b" + old.replace(/[-]/g, "\\-") + "\\.", "g");
    for (const o of wf.steps) {
      o.after = (o.after || []).map((a) => (a === old ? nid : a));
      if (o.not_by) o.not_by = o.not_by.map((a) => (a === old ? nid : a));
      if (o.review_of === old) o.review_of = nid;
      if (o.context) o.context = o.context.map((c) => (c === old ? nid : c.startsWith(old + ".") ? nid + c.slice(old.length) : c));
      for (const f of ["when", "agent_criteria"]) if (o[f]) o[f] = o[f].replace(re, nid + ".");
      if (o.params) o.params = JSON.parse(JSON.stringify(o.params).replace(new RegExp("\\{\\{\\s*" + old + "\\.", "g"), "{{" + nid + "."));
    }
    selected = nid; changed(true);
  }
  async function removeStep(s, trigger) {
    const ok = await askConfirm(trigger, `Remove the step "${s.title || s.id}"? Steps that waited on it will no longer wait.`,
      { danger: true, title: "Remove this step?", label: "Remove step" });
    if (!ok) return;
    wf.steps = wf.steps.filter((o) => o !== s);
    for (const o of wf.steps) {
      o.after = (o.after || []).filter((a) => a !== s.id);
      if (o.not_by) o.not_by = o.not_by.filter((a) => a !== s.id);
      if (o.review_of === s.id) delete o.review_of;
      if (o.context) o.context = o.context.filter((c) => c !== s.id && !c.startsWith(s.id + "."));
    }
    selected = null; changed(true);
  }
  function addStep(kind) {
    let n = wf.steps.length + 1, id;
    do { id = `step-${n++}`; } while (stepById(id));
    const prev = selected ? [selected] : wf.steps.length ? [wf.steps[wf.steps.length - 1].id] : [];
    const role = cat.roles.length ? cat.roles[0].id : undefined;
    const s = { id, title: kind === "gate" ? "Decide" : kind === "automation" ? "Do it automatically" : "New task", kind, after: prev };
    if (kind === "task") Object.assign(s, { role, risk: "low", fields: [{ name: "done", type: "boolean", label: "Done" }] });
    if (kind === "gate") Object.assign(s, { role, signer: "person", outcomes: ["approve", "decline"], meaning: "I approve this" });
    if (kind === "automation") Object.assign(s, { action: "message.send", params: {} });
    wf.steps.push(s); selected = id; changed(true);
  }

  // ---- panels
  const put = (...k) => panel.append(...k.filter((x) => x !== null && x !== undefined && x !== false));
  function renderPanel() {
    panel.textContent = "";
    const s = selected && stepById(selected);
    if (!s) return workflowPanel();
    const others = wf.steps.filter((o) => o !== s);
    const roles = cat.roles.map((r) => [r.id, r.name]);
    const head = el("h2", {}, el("span", { class: `ki ${s.kind}` }, s.kind === "gate" ? "D" : (s.kind || "t")[0].toUpperCase()),
      "Step", el("span", { class: "sp" }),
      el("button", { type: "button", class: "subtle", onclick: () => { selected = null; redraw(); renderPanel(); } }, "Workflow settings"));
    put(head,
      text("Title", s, "title"),
      text("Id", s, "id", { onchange: (v) => renameStep(s, v), help: "lower-case letters, digits and dashes; other steps refer to it" }),
      select("Kind", s, "kind", [["task", "Task: someone fills in a form"], ["gate", "Decision: someone signs an outcome"],
        ["automation", "Automation: a deterministic action"]], { rebuild: true }),
      s.kind !== "automation" ? select("Role", s, "role", roles, { blank: "choose a role", help: "steps name roles, never people" }) : null,
      checks("Starts after", s, "after", others.map((o) => [o.id, o.title || o.id]), { none: "no other steps yet", help: "nothing ticked: it starts as soon as the run does" }),
      text("Only when", s, "when", { placeholder: "e.g. triage.category == 'urgent'", help: "a condition on earlier steps or the input; otherwise the step is skipped" }));
    const blk = s.block && (cat.blocks || {})[s.block];
    if (s.kind !== "gate" && blocksFor(s.kind).length) {
      put(el("label", {}, "Use a block",
        el("select", { onchange: (ev) => chooseBlock(s, ev.target.value) },
          el("option", { value: "" }, s.kind === "task" ? "no: write the task here" : "no: use an action"),
          blocksFor(s.kind).map((b) => el("option", { value: b.id, selected: s.block === b.id ? "selected" : null },
            `${b.title} (${b.runtime === "prompt" ? "agent prompt" : b.runtime === "python" ? "Python" : "engine"})`))),
        el("small", {}, s.kind === "task" ? "a prompt block: an agent with the block in its profile carries it out"
          : "a Python or engine block: deterministic, approved and signed")));
    }
    if (blk) put(paramsEditor(s, blk));
    if (s.kind !== "automation") {
      const skills = Object.values(cat.skills).map((k) => [k.id, k.title]);
      put(el("fieldset", {}, el("legend", {}, "Who may do it"),
        select("Risk", s, "risk", [["low", "low: an agent's work goes ahead"], ["medium", "medium: goes ahead, the sponsor is told"],
          ["high", "high: held until the sponsor approves"]], {}),
        blk ? null : select("Agent skill", s, "skill", skills, { blank: "none: people only", help: "an agent can take the step only with a skill it has" }),
        s.kind === "task" ? bool("People only, never an agent", s, "persons_only", { rebuild: false }) : null,
        checks("Not by whoever did", s, "not_by", others.map((o) => [o.id, o.title || o.id]).concat([["run", "started this work"], ["record.editors", "wrote or changed its record"]]), { none: "no other steps" })));
    }
    if (s.kind === "gate") {
      put(el("fieldset", {}, el("legend", {}, "Decision and signature"),
        csv("Outcomes", s, "outcomes", { placeholder: "approve, decline" }),
        text("Meaning of the signature", s, "meaning", { placeholder: "I have reviewed this and I am responsible for it" }),
        select("Who may sign", s, "signer", [["person", "a person only"], ["any", "a person, or an agent within criteria"]], { rebuild: true }),
        s.signer === "any" ? text("Agents may decide only when", s, "agent_criteria", { placeholder: "e.g. code.amount <= 80" }) : null));
    }
    if (s.kind === "task" && blk) {
      put(el("fieldset", {}, el("legend", {}, "Instructions"),
        el("small", { class: "muted" }, "The block's prompt is the agent's instructions; a person who takes the step sees it too."),
        select("Review the draft of", s, "review_of", others.map((o) => [o.id, o.title || o.id]), { blank: "nothing", help: "the person sees that step's answer, prefilled" })));
    } else if (s.kind === "task") {
      put(el("fieldset", {}, el("legend", {}, "Instructions"),
        text("Shown to whoever does it", s, "instructions", { area: true, rows: 3 }),
        select("Review the draft of", s, "review_of", others.map((o) => [o.id, o.title || o.id]), { blank: "nothing", help: "the person sees that step's answer, prefilled" })));
      const sk = s.skill && cat.skills[s.skill];
      put(fieldsEditor(s, "fields", "What it produces",
        sk ? `Leave empty to use the skill's fields: ${sk.output.join(", ")}.` : ""));
    }
    if (s.kind !== "automation") {
      const ctxOpts = [...cat.data_classes.map((d) => ["record:" + d.id, d.name + " records"]), ["input", "the run's input"],
        ...cat.providers.map((p) => ["provider:" + p, p]), ...others.map((o) => [o.id, "output of " + (o.title || o.id)])];
      put(el("fieldset", {}, el("legend", {}, "What it reads"),
        checks("Context", s, "context", ctxOpts, { help: "data classes travel with data: whoever takes this step must be allowed to read all of it. To pass one field only, type it below." }),
        csv("Single fields", { get v() { return (s.context || []).filter((c) => !ctxOpts.some(([v]) => v === c)); },
          set v(x) { s.context = (s.context || []).filter((c) => ctxOpts.some(([v]) => v === c)).concat(x); } }, "v",
          { placeholder: "e.g. edit.diagnosis_codes, input.medication" })));
    } else if (!blk) {
      const params = el("textarea", { rows: 8, class: "code", spellcheck: "false",
        oninput: (ev) => { try { s.params = JSON.parse(ev.target.value || "{}"); ev.target.classList.remove("bad-json"); changed(false); }
          catch (e) { ev.target.classList.add("bad-json"); } } });
      params.value = JSON.stringify(s.params || {}, null, 2);
      put(el("fieldset", {}, el("legend", {}, "Action"),
        select("Action", s, "action", cat.actions.map((a) => [a, a]), { blank: "choose" }),
        el("label", {}, "Parameters (JSON)", params,
          el("small", {}, "{{run.record}}, {{input.x}}, {{step.field}} are filled in from the run, as data.")),
        ));
    }
    put(el("p", {}, el("button", { type: "button", class: "danger", onclick: (ev) => removeStep(s, ev.currentTarget) }, "Remove step")));
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
        text("Or when this event happens", wf.trigger, "event", { placeholder: "e.g. lab.result-received" })),
      fieldsEditor(wf, "input", "What the starter gives"),
      el("p", { class: "muted" }, "Select a step in the diagram to edit it, or add one."));
  }

  // ---- tabs, toolbar, submit
  root.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => addStep(b.dataset.add)));
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
    if (b.dataset.tab === "toml") {
      tomlBox.value = toToml(wf); tab = "toml";
    } else {
      lastSource = null;
      const res = await check();
      if (!res || !res.data) { status.prepend("Fix the TOML before going back to the diagram. "); syncTabs(); return; }
      wf = res.data; wf.steps = wf.steps || []; selected = null; tab = "visual"; redraw(); renderPanel();
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
  svg.addEventListener("click", () => { selected = null; redraw(); renderPanel(); });
  form.addEventListener("submit", () => { sourceIn.value = currentSource(); dirty = false; });
  redraw(); renderPanel();
  if (boot.problems && boot.problems.length) { showProblems(boot.problems); lastSource = currentSource(); } else check();
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
  function close() { if (dlg) { dlg.close(); dlg.remove(); dlg = null; } dirty = false; }
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
    dlg.addEventListener("close", () => { if (dlg) { dlg.remove(); dlg = null; } dirty = false; });
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
  function show(html, title, refused) {
    frame(title);
    dlg.querySelector("#fm-title").textContent = title;
    const body = dlg.querySelector(".fm-body");
    body.innerHTML = html;
    dirty = !!refused;  // a refused form still holds what was typed
    body.querySelectorAll("textarea.markdown[id]").forEach(mdEditor);
    formTabs(body);
    kindFields(body);
    body.querySelectorAll("[data-modal-close]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); close(); }));
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
  async function open(href, words) {
    if (busy) return;  // a second click while the first loads does nothing
    busy = true;
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
    f.dispatchEvent(new Event("modal-submit"));
    const btn = f.querySelector("button:not([type=button])");
    if (btn) btn.disabled = true;
    try {
      const data = new URLSearchParams(new FormData(f, ev.submitter || undefined));
      const res = await fetch(f.action, { method: "POST", body: data, credentials: "same-origin" });
      if (res.ok && res.redirected) { dirty = false; location.href = res.url; return; }
      if (res.ok) { dirty = false; location.reload(); return; }
      const got = extract(await res.text());
      show(got.html || "<p>That did not work.</p>", dlg ? dlg.querySelector("#fm-title").textContent : got.title, true);
    } catch (e) {
      if (btn) btn.disabled = false;
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
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".avatar-menu").forEach(avatarMenu);
  formModal();
  document.querySelectorAll("textarea.markdown[id]").forEach(mdEditor);
  formTabs(document);
  kindFields(document);
  document.querySelectorAll("svg[data-flow]").forEach((svg) => {
    const src = document.getElementById(svg.dataset.flow);
    if (src) drawFlow(svg, JSON.parse(src.textContent));
  });
  const ed = document.getElementById("wf-editor");
  if (ed) editor(ed);
});
})();
"""
