import json, html
from graphviz import Digraph

schema = json.load(open('/tmp/erd_schema.json'))

GROUPS = {
    'projects':'op','bids':'op','submissions':'op','submission_items':'op',
    'project_attr_defs':'attr','project_attrs':'attr',
    'catalog_categories':'cat','catalog_items':'cat',
    'catalog_clusters':'cat','catalog_cluster_members':'cat','catalog_suggestions':'cat',
    'price_history':'aux','bid_watchlist':'aux','domains':'mst','users':'mst',
}
HEADER = {'op':'#1e40af','attr':'#0369a1','cat':'#15803d','aux':'#6d28d9','mst':'#334155'}
TITLE  = {'op':'입찰 데이터','attr':'기준정보','cat':'카탈로그','aux':'부가','mst':'마스터'}

NOTE = {
  ('users','llm_api_key_enc'):'암호화',('users','role'):'RBAC',
  ('submissions','compare_units'):'JSON·비교단위',('submissions','fx_rate_used'):'대표환율',
  ('submissions','deleted_at'):'소프트삭제',('submissions','file_format'):'xlsx/pdf/docx',
  ('submission_items','path'):'상위경로',('submission_items','name_normalized'):'잎이름',
  ('submission_items','unit_price'):'원화',('submission_items','unit_price_orig'):'원통화',
  ('submission_items','fx_rate_used'):'항목환율',('submission_items','is_nego'):'특별네고',
  ('bids','category_order'):'JSON·분류순서',('bids','domain'):'도메인',
  ('projects','domain'):'도메인',('project_attr_defs','domain'):'공통/전용',
  ('catalog_categories','parent_id'):'자기참조',('catalog_items','aliases'):'영/한/약어',
  ('catalog_clusters','status'):'상태',('catalog_clusters','compare_level'):'비교레벨',
  ('catalog_cluster_members','role'):'대표/멤버',
}
CORE_COLS = ('bid_id','matched_catalog_item_id','name','vendor_name','representative_name',
             'name_canonical','label','amount','status','similarity_score','role','email',
             'file_format','compare_level','is_nego')

def esc(s): return html.escape(str(s))
FK_COLS = {(f['from_table'], f['from_col']) for f in schema['fks']}

def keep_col(t,col):
    cn=col['name']
    return col['pk'] or (t,cn) in FK_COLS or (t,cn) in NOTE or cn in CORE_COLS

dot = Digraph('QDBT_ERD', format='png')
dot.attr(rankdir='LR', splines='spline', overlap='false', nodesep='0.4', ranksep='1.25',
         bgcolor='#fcfcfd', fontname='Helvetica', pad='0.5', label=(
         '<<FONT POINT-SIZE="20"><B>QDBT — 데이터 모델 (ERD)</B></FONT>'
         '<BR/><FONT POINT-SIZE="10" COLOR="#64748b">조달 견적 비교 도구 · SQLite 15 tables · 실제 스키마 기준</FONT>>'),
         labelloc='t')
dot.attr('node', shape='none', fontname='Helvetica')
dot.attr('edge', color='#94a3b8', arrowsize='0.8', penwidth='1.3')

# 그룹별 클러스터(subgraph)로 시각적 묶음
from collections import defaultdict
by_group = defaultdict(list)
for t in schema['tables']: by_group[GROUPS.get(t,'aux')].append(t)

def make_table_node(sub, t):
    cols = schema['tables'][t]
    g = GROUPS.get(t,'aux'); hc = HEADER[g]
    rows = [f'<TR><TD BGCOLOR="{hc}" COLSPAN="2" CELLPADDING="6"><FONT COLOR="white" POINT-SIZE="12"><B>{esc(t)}</B></FONT></TD></TR>']
    for col in cols:
        if not keep_col(t,col): continue
        cn=col['name']; is_pk=col['pk']; is_fk=(t,cn) in FK_COLS
        badge = ('◆' if is_pk else ('◇' if is_fk else '·'))
        bcolor = '#1e40af' if is_pk else ('#7c3aed' if is_fk else '#cbd5e1')
        note = NOTE.get((t,cn),'')
        note_html = f'&nbsp;<FONT COLOR="#94a3b8" POINT-SIZE="8"><I>{esc(note)}</I></FONT>' if note else ''
        bg = ' BGCOLOR="#eff6ff"' if is_pk else (' BGCOLOR="#faf5ff"' if is_fk else '')
        rows.append(
          f'<TR><TD ALIGN="LEFT"{bg} PORT="{esc(cn)}" CELLPADDING="3">'
          f'<FONT COLOR="{bcolor}" POINT-SIZE="9">{badge}</FONT> <FONT POINT-SIZE="9">{esc(cn)}</FONT></TD>'
          f'<TD ALIGN="LEFT"{bg} CELLPADDING="3"><FONT COLOR="#b0bac9" POINT-SIZE="8">{esc(col["type"])}</FONT>{note_html}</TD></TR>')
    hidden = sum(1 for col in cols if not keep_col(t,col))
    if hidden>0:
        rows.append(f'<TR><TD COLSPAN="2" ALIGN="LEFT" CELLPADDING="3"><FONT COLOR="#cbd5e1" POINT-SIZE="8">⋯ +{hidden} columns</FONT></TD></TR>')
    label = '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" COLOR="#e2e8f0">' + ''.join(rows) + '</TABLE>>'
    sub.node(t, label=label)

for g, tbls in by_group.items():
    with dot.subgraph(name=f'cluster_{g}') as sub:
        sub.attr(style='rounded,filled', color=HEADER[g]+'22', fillcolor=HEADER[g]+'0a',
                 label=f'  {TITLE[g]}  ', fontcolor=HEADER[g], fontsize='13', fontname='Helvetica-Bold', margin='12')
        for t in tbls: make_table_node(sub, t)

# 물리 FK (실선 + 카디널리티 crow's foot)
for f in schema['fks']:
    dot.edge(f"{f['from_table']}:{f['from_col']}", f"{f['to_table']}:{f['to_col']}",
             arrowhead='crow', arrowtail='tee', dir='both', color='#64748b')

# 논리 관계 (점선)
LOGICAL = [
    ('catalog_clusters','bid_id','bids','bid_id'),
    ('catalog_cluster_members','catalog_item_id','catalog_items','catalog_item_id'),
    ('submission_items','catalog_item_id','catalog_items','catalog_item_id'),
    ('price_history','catalog_item_id','catalog_items','catalog_item_id'),
    ('bid_watchlist','catalog_item_id','catalog_items','catalog_item_id'),
    ('catalog_suggestions','matched_catalog_item_id','catalog_items','catalog_item_id'),
]
for ft,fc,tt,tc in LOGICAL:
    dot.edge(f"{ft}:{fc}", f"{tt}:{tc}", style='dashed', color='#c4b5fd',
             arrowhead='crow', arrowtail='tee', dir='both')

# 범례
legend = '''<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="2" COLOR="#e2e8f0" CELLPADDING="3" BGCOLOR="white">
<TR><TD COLSPAN="2"><FONT POINT-SIZE="10"><B>범례</B></FONT></TD></TR>
<TR><TD ALIGN="LEFT"><FONT COLOR="#1e40af">◆</FONT></TD><TD ALIGN="LEFT"><FONT POINT-SIZE="9">Primary Key</FONT></TD></TR>
<TR><TD ALIGN="LEFT"><FONT COLOR="#7c3aed">◇</FONT></TD><TD ALIGN="LEFT"><FONT POINT-SIZE="9">Foreign Key</FONT></TD></TR>
<TR><TD ALIGN="LEFT"><FONT COLOR="#64748b">──</FONT></TD><TD ALIGN="LEFT"><FONT POINT-SIZE="9">물리 FK 제약</FONT></TD></TR>
<TR><TD ALIGN="LEFT"><FONT COLOR="#c4b5fd">╌╌</FONT></TD><TD ALIGN="LEFT"><FONT POINT-SIZE="9">논리 관계(제약 없음)</FONT></TD></TR>
<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9">⧙</FONT></TD><TD ALIGN="LEFT"><FONT POINT-SIZE="9">1 : N (crow's foot)</FONT></TD></TR>
</TABLE>>'''
dot.node('__legend__', label=legend, shape='none')

dot.attr(dpi='170')
out = dot.render('/tmp/QDBT_ERD_v2', cleanup=True)
print("렌더 완료:", out)
