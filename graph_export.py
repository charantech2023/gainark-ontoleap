"""
GainARK OntoLeap — Interactive Standalone Network Graph Exporter (PyVis / Vis.js Paradigm)

Generates a 100% self-contained, offline-capable interactive network graph in HTML format.
Enables stakeholders to visualize their website's topic authority hubs, spoke pages,
relational semantic triples, and AI-predicted links with dynamic force-directed physics.
"""

import html
import json


def _json_for_script(value) -> str:
    """
    Serialise `value` as JSON that is safe to embed inside an HTML <script> block.

    Plain json.dumps output can terminate the script element ("</script>") or introduce
    markup, so the characters that make that possible are emitted as JSON unicode
    escapes. The result parses identically as JavaScript.
    """
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from models import ClusterTopology, SemanticTriple, PredictedLink


def generate_standalone_graph_html(
    domain: str,
    topology: Optional[ClusterTopology] = None,
    triples: Optional[List[SemanticTriple]] = None,
    topic_hubs: Optional[Dict[str, str]] = None,
    predicted_links: Optional[List[PredictedLink]] = None
) -> str:
    """
    Generates a standalone, fully-interactive Vis.js HTML document.
    """
    triples = triples or []
    topic_hubs = topic_hubs or {}
    predicted_links = predicted_links or []

    vis_nodes: List[Dict[str, Any]] = []
    vis_edges: List[Dict[str, Any]] = []
    seen_nodes: set = set()

    # 1. Base Topology Nodes & Edges
    if topology and topology.nodes:
        for n in topology.nodes:
            if n.id in seen_nodes:
                continue
            seen_nodes.add(n.id)

            color = "#3B82F6"  # Blue spoke
            border_color = "#1D4ED8"
            shape = "dot"
            size = n.value or 16

            if n.type == "hub" or n.group == "hub":
                color = "#F59E0B"  # Gold hub
                border_color = "#B45309"
                shape = "hexagon"
                size = 28
            elif n.type == "entity":
                color = "#8B5CF6"  # Purple entity
                border_color = "#6D28D9"
                shape = "diamond"
                size = 20

            vis_nodes.append({
                "id": n.id,
                "label": n.label,
                "title": f"<b>{n.label}</b><br>Type: {n.type}<br>URL: {n.url or n.id}",
                "color": {"background": color, "border": border_color},
                "shape": shape,
                "size": size,
                "font": {"color": "#1E293B", "size": 12, "face": "Inter, sans-serif"},
                "node_type": n.type,
                "url": n.url or n.id
            })

        for e in topology.edges:
            src = getattr(e, 'source', None) or getattr(e, 'from_node', None)
            tgt = getattr(e, 'target', None) or getattr(e, 'to_node', None)
            vis_edges.append({
                "from": src,
                "to": tgt,
                "label": e.label or "",
                "arrows": "to",
                "color": {"color": "#CBD5E1", "highlight": "#3B82F6"},
                "font": {"size": 9, "color": "#64748B", "align": "middle"},
                "width": 1.5,
                "smooth": {"type": "continuous"}
            })

    # 2. Add Triples as Graph Edges and Nodes if not present
    for t in triples:
        s_id = f"ent_{t.subject.lower().replace(' ', '_')}"
        o_id = f"ent_{t.object.lower().replace(' ', '_')}"

        if s_id not in seen_nodes:
            seen_nodes.add(s_id)
            vis_nodes.append({
                "id": s_id,
                "label": t.subject,
                "title": f"<b>Subject:</b> {t.subject}",
                "color": {"background": "#F59E0B", "border": "#B45309"},
                "shape": "hexagon",
                "size": 24,
                "font": {"color": "#0F172A", "size": 13, "face": "Inter, sans-serif", "bold": True},
                "node_type": "subject",
                "url": domain
            })

        if o_id not in seen_nodes:
            seen_nodes.add(o_id)
            pred_low = t.predicate.lower()
            if "integrat" in pred_low:
                color, border, shape = "#10B981", "#047857", "triangle"
                ntype = "integration"
            elif "compl" in pred_low or "standard" in pred_low:
                color, border, shape = "#EF4444", "#B91C1C", "square"
                ntype = "compliance"
            else:
                color, border, shape = "#8B5CF6", "#6D28D9", "diamond"
                ntype = "capability"

            ev_text = getattr(t, 'evidence_sentence', None) or getattr(t, 'evidence', None) or 'Extracted'
            vis_nodes.append({
                "id": o_id,
                "label": t.object,
                "title": f"<b>{t.object}</b><br>Predicate: {t.predicate}<br>Evidence: {ev_text}",
                "color": {"background": color, "border": border},
                "shape": shape,
                "size": 18,
                "font": {"color": "#1E293B", "size": 11, "face": "Inter, sans-serif"},
                "node_type": ntype,
                "url": ""
            })

        vis_edges.append({
            "from": s_id,
            "to": o_id,
            "label": t.predicate,
            "arrows": "to",
            "color": {"color": "#94A3B8", "highlight": "#6366F1"},
            "font": {"size": 10, "color": "#475569", "align": "middle"},
            "width": 2.0
        })

    # 3. Add AI-Predicted Links as Cyan Nodes & Dashed Edges
    for pl in predicted_links[:15]:
        s_id = f"ent_{pl.subject.lower().replace(' ', '_')}"
        p_id = f"pred_{pl.object.lower().replace(' ', '_')}"

        if s_id not in seen_nodes:
            seen_nodes.add(s_id)
            vis_nodes.append({
                "id": s_id,
                "label": pl.subject,
                "title": f"<b>{pl.subject}</b>",
                "color": {"background": "#F59E0B", "border": "#B45309"},
                "shape": "hexagon",
                "size": 24,
                "font": {"color": "#0F172A", "size": 13, "face": "Inter, sans-serif"},
                "node_type": "subject",
                "url": domain
            })

        if p_id not in seen_nodes:
            seen_nodes.add(p_id)
            vis_nodes.append({
                "id": p_id,
                "label": f"⚡ {pl.object}",
                "title": f"<b>AI Predicted Missing Node</b><br>{pl.object} ({int(pl.confidence*100)}% Confidence)<br>{pl.reasoning}",
                "color": {"background": "#06B6D4", "border": "#0891B2"},
                "shape": "star",
                "size": 22,
                "font": {"color": "#0891B2", "size": 11, "face": "Inter, sans-serif", "bold": True},
                "node_type": "predicted",
                "url": pl.wikidata_url or ""
            })

        vis_edges.append({
            "from": s_id,
            "to": p_id,
            "label": f"{pl.predicate} ({int(pl.confidence*100)}%)",
            "arrows": "to",
            "dashes": True,
            "color": {"color": "#06B6D4", "highlight": "#0891B2"},
            "font": {"size": 9, "color": "#0891B2", "align": "middle"},
            "width": 2.0
        })

    # json.dumps does not escape "</script>", so any node label carrying that sequence
    # closes the script element and everything after it is parsed as markup. Node labels
    # come from crawled pages and from caller-supplied triples, so they are untrusted.
    # Escaping the three characters that can start a tag or an entity keeps the payload
    # valid JSON while making it inert inside a script block.
    nodes_json = _json_for_script(vis_nodes)
    edges_json = _json_for_script(vis_edges)

    # The domain is interpolated into HTML text, not into JSON, and needs escaping too.
    safe_domain = html.escape(str(domain or ""), quote=True)

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GainARK OntoLeap — Interactive Knowledge Graph: {safe_domain}</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0F172A;
            color: #F8FAFC;
            overflow: hidden;
            width: 100vw;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        header {{
            background: rgba(15, 23, 42, 0.95);
            border-bottom: 1px solid #334155;
            padding: 12px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            z-index: 10;
        }}
        .brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .logo {{
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #6366F1, #8B5CF6);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            color: white;
            font-size: 16px;
        }}
        .title {{
            font-size: 16px;
            font-weight: 700;
            color: #F8FAFC;
        }}
        .domain-tag {{
            font-size: 12px;
            font-family: 'JetBrains Mono', monospace;
            background: #1E293B;
            border: 1px solid #475569;
            padding: 2px 8px;
            border-radius: 6px;
            color: #38BDF8;
        }}
        .toolbar {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .btn {{
            background: #1E293B;
            border: 1px solid #475569;
            color: #E2E8F0;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn:hover {{
            background: #334155;
            border-color: #64748B;
            color: #FFFFFF;
        }}
        .btn-primary {{
            background: #4F46E5;
            border-color: #6366F1;
            color: white;
        }}
        .btn-primary:hover {{
            background: #4338CA;
        }}
        #search-box {{
            background: #1E293B;
            border: 1px solid #475569;
            color: white;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            width: 180px;
            outline: none;
        }}
        #search-box:focus {{
            border-color: #38BDF8;
        }}
        #filter-select {{
            background: #1E293B;
            border: 1px solid #475569;
            color: white;
            padding: 6px 10px;
            border-radius: 8px;
            font-size: 12px;
            outline: none;
        }}
        .main-container {{
            position: relative;
            flex: 1;
            width: 100%;
            height: 100%;
        }}
        #network {{
            width: 100%;
            height: 100%;
            background: #090D16;
        }}
        .legend {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            background: rgba(15, 23, 42, 0.9);
            backdrop-filter: blur(8px);
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 12px 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            font-size: 11px;
            z-index: 5;
            pointer-events: none;
        }}
        .legend-title {{
            font-weight: 700;
            color: #94A3B8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-size: 10px;
            margin-bottom: 2px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            color: #CBD5E1;
        }}
        .legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }}
        .details-panel {{
            position: absolute;
            top: 20px;
            right: 20px;
            width: 320px;
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(12px);
            border: 1px solid #334155;
            border-radius: 14px;
            padding: 18px;
            display: none;
            flex-direction: column;
            gap: 12px;
            z-index: 20;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .details-header {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 8px;
            border-bottom: 1px solid #334155;
            padding-bottom: 10px;
        }}
        .details-name {{
            font-size: 15px;
            font-weight: 700;
            color: #F8FAFC;
            word-break: break-word;
        }}
        .close-btn {{
            background: transparent;
            border: none;
            color: #94A3B8;
            cursor: pointer;
            font-size: 16px;
            line-height: 1;
        }}
        .close-btn:hover {{ color: white; }}
        .badge {{
            display: inline-block;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
        }}
        .details-row {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 12px;
        }}
        .details-label {{
            color: #64748B;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }}
        .details-value {{
            color: #E2E8F0;
            word-break: break-all;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
        }}
        .details-value a {{
            color: #38BDF8;
            text-decoration: none;
        }}
        .details-value a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <div class="logo">G</div>
            <div>
                <span class="title">GainARK OntoLeap</span>
                <span style="color:#64748B; margin: 0 6px;">/</span>
                <span style="font-size: 13px; color: #94A3B8;">Interactive Knowledge Graph</span>
            </div>
            <span class="domain-tag">{safe_domain}</span>
        </div>
        <div class="toolbar">
            <input type="text" id="search-box" placeholder="Search nodes..." onkeyup="searchNodes()">
            <select id="filter-select" onchange="filterNodes()">
                <option value="all">Show All Nodes</option>
                <option value="hub">Topic Hubs Only</option>
                <option value="predicted">⚡ AI Predictions Only</option>
                <option value="integration">Integrations Only</option>
                <option value="compliance">Compliance Only</option>
            </select>
            <button class="btn" onclick="togglePhysics()" id="physics-btn">⏸ Pause Physics</button>
            <button class="btn" onclick="network.fit({{ animation: true }})">🎯 Fit View</button>
        </div>
    </header>

    <div class="main-container">
        <div id="network"></div>

        <div class="legend">
            <div class="legend-title">Graph Topography</div>
            <div class="legend-item"><span class="legend-dot" style="background:#F59E0B"></span> Topic Authority Hub</div>
            <div class="legend-item"><span class="legend-dot" style="background:#3B82F6"></span> Spoke Content Page</div>
            <div class="legend-item"><span class="legend-dot" style="background:#8B5CF6"></span> Canonical Entity</div>
            <div class="legend-item"><span class="legend-dot" style="background:#10B981"></span> Software Integration</div>
            <div class="legend-item"><span class="legend-dot" style="background:#EF4444"></span> Compliance Standard</div>
            <div class="legend-item"><span class="legend-dot" style="background:#06B6D4"></span> ⚡ AI Predicted Link</div>
        </div>

        <div class="details-panel" id="details-panel">
            <div class="details-header">
                <div>
                    <span class="badge" id="node-type-badge">HUB</span>
                    <div class="details-name" id="node-name">Revenue Recognition</div>
                </div>
                <button class="close-btn" onclick="closeDetails()">&times;</button>
            </div>
            <div class="details-row">
                <span class="details-label">Node Identifier</span>
                <span class="details-value" id="node-id"></span>
            </div>
            <div class="details-row">
                <span class="details-label">URL / Anchor Target</span>
                <span class="details-value" id="node-url"></span>
            </div>
            <div class="details-row">
                <span class="details-label">Connected Graph Links</span>
                <span class="details-value" id="node-degree"></span>
            </div>
        </div>
    </div>

    <script type="text/javascript">
        const rawNodes = {nodes_json};
        const rawEdges = {edges_json};

        let nodes = new vis.DataSet(rawNodes);
        let edges = new vis.DataSet(rawEdges);

        const container = document.getElementById('network');
        const data = {{ nodes: nodes, edges: edges }};
        const options = {{
            nodes: {{
                shape: 'dot',
                scaling: {{ min: 14, max: 36 }},
                borderWidth: 2,
                shadow: true
            }},
            edges: {{
                arrows: {{ to: {{ enabled: true, scaleFactor: 0.6 }} }},
                selectionWidth: 3,
                smooth: {{
                    type: 'continuous',
                    roundness: 0.4
                }}
            }},
            physics: {{
                solver: 'barnesHut',
                barnesHut: {{
                    gravitationalConstant: -3500,
                    centralGravity: 0.25,
                    springLength: 95,
                    springConstant: 0.04,
                    damping: 0.09
                }},
                stabilization: {{ iterations: 150 }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 150,
                navigationButtons: true,
                keyboard: true
            }}
        }};

        const network = new vis.Network(container, data, options);
        let physicsRunning = true;

        function togglePhysics() {{
            physicsRunning = !physicsRunning;
            network.setOptions({{ physics: {{ enabled: physicsRunning }} }});
            document.getElementById('physics-btn').innerText = physicsRunning ? '⏸ Pause Physics' : '▶ Enable Physics';
        }}

        network.on('click', function(params) {{
            if (params.nodes.length > 0) {{
                const nodeId = params.nodes[0];
                const nodeData = nodes.get(nodeId);
                showDetails(nodeData);
            }} else {{
                closeDetails();
            }}
        }});

        function showDetails(node) {{
            const panel = document.getElementById('details-panel');
            document.getElementById('node-name').innerText = node.label;
            document.getElementById('node-id').innerText = node.id;
            const urlEl = document.getElementById('node-url');
            if (node.url && node.url.startsWith('http')) {{
                urlEl.innerHTML = `<a href="${{node.url}}" target="_blank">${{node.url}}</a>`;
            }} else {{
                urlEl.innerText = node.url || 'Internal Ontological Node';
            }}
            const connEdges = network.getConnectedEdges(node.id);
            document.getElementById('node-degree').innerText = `${{connEdges.length}} connected relations`;

            const badge = document.getElementById('node-type-badge');
            badge.innerText = (node.node_type || 'node').toUpperCase();
            if (node.node_type === 'hub') {{
                badge.style.background = '#F59E0B'; badge.style.color = '#000';
            }} else if (node.node_type === 'predicted') {{
                badge.style.background = '#06B6D4'; badge.style.color = '#000';
            }} else if (node.node_type === 'integration') {{
                badge.style.background = '#10B981'; badge.style.color = '#fff';
            }} else if (node.node_type === 'compliance') {{
                badge.style.background = '#EF4444'; badge.style.color = '#fff';
            }} else {{
                badge.style.background = '#6366F1'; badge.style.color = '#fff';
            }}

            panel.style.display = 'flex';
        }}

        function closeDetails() {{
            document.getElementById('details-panel').style.display = 'none';
        }}

        function searchNodes() {{
            const term = document.getElementById('search-box').value.toLowerCase().trim();
            if (!term) return;
            const matches = nodes.get({{
                filter: function(item) {{
                    return item.label.toLowerCase().includes(term);
                }}
            }});
            if (matches.length > 0) {{
                network.focus(matches[0].id, {{
                    scale: 1.3,
                    animation: {{ duration: 500, easingFunction: 'easeInOutQuad' }}
                }});
                network.selectNodes([matches[0].id]);
                showDetails(matches[0]);
            }}
        }}

        function filterNodes() {{
            const val = document.getElementById('filter-select').value;
            if (val === 'all') {{
                nodes.clear();
                nodes.add(rawNodes);
            }} else {{
                const filtered = rawNodes.filter(n => n.node_type === val);
                nodes.clear();
                nodes.add(filtered);
            }}
            network.fit({{ animation: true }});
        }}
    </script>
</body>
</html>
"""
    return html_template
