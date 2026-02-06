#!/usr/bin/env python3
"""
Website Crawler Agent - Site Mapping Tool
Built with OpenHands Software Agent SDK

Usage:
    python site_mapper_agent.py <url> [--depth N]
    
Example:
    python site_mapper_agent.py https://example.com
    python site_mapper_agent.py https://example.com --depth 2

Environment Variables:
    LLM_API_KEY: Your LLM API key (required)
    LLM_BASE_MODEL: Model to use (default: openhands/claude-sonnet-4-5-20250929)
"""

import argparse
import json
import os
import sys
import time
from collections import deque
from collections.abc import Sequence
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import Field, SecretStr

from openhands.sdk import (
    LLM,
    Action,
    Agent,
    Conversation,
    ImageContent,
    Observation,
    TextContent,
    ToolDefinition,
    get_logger,
)
from openhands.sdk.tool import Tool, ToolExecutor, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool

logger = get_logger(__name__)

# ============================================================================
# Custom Crawl Website Tool - Action
# ============================================================================

class CrawlWebsiteAction(Action):
    """Action to crawl a website and build a sitemap."""
    url: str = Field(description="The starting URL to crawl")
    max_depth: int = Field(default=2, description="Maximum depth to crawl (1-2 recommended)")


# ============================================================================
# Custom Crawl Website Tool - Observation
# ============================================================================

class CrawlWebsiteObservation(Observation):
    """Observation containing the crawl results."""
    url_tree: dict = Field(default_factory=dict, description="Hierarchical URL structure")
    total_urls: int = Field(default=0, description="Total unique URLs discovered")
    domain: str = Field(default="", description="The crawled domain")
    errors: list[str] = Field(default_factory=list, description="Errors encountered during crawl")
    output_file: str = Field(default="", description="Path to generated sitemap HTML")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Format the observation for the LLM."""
        if not self.total_urls:
            error_text = "\n".join(self.errors) if self.errors else "Unknown error"
            return [TextContent(text=f"Crawl failed:\n{error_text}")]
        
        summary = f"""✅ Website Crawl Complete!

📊 **Summary:**
- Domain: {self.domain}
- Total URLs discovered: {self.total_urls}
- Crawl depth: {self._get_max_depth(self.url_tree)}
- Output file: {self.output_file}

🌳 **Site Structure (top level):**
"""
        # Show top-level pages
        if "children" in self.url_tree:
            for child in self.url_tree.get("children", [])[:10]:
                summary += f"  └─ {child.get('name', child.get('url', 'Unknown'))}\n"
            if len(self.url_tree.get("children", [])) > 10:
                summary += f"  ... and {len(self.url_tree['children']) - 10} more pages\n"
        
        if self.errors:
            summary += f"\n⚠️ **Warnings ({len(self.errors)}):**\n"
            for err in self.errors[:5]:
                summary += f"  - {err}\n"
        
        return [TextContent(text=summary)]
    
    def _get_max_depth(self, node: dict, current: int = 0) -> int:
        """Calculate the maximum depth of the URL tree."""
        if "children" not in node or not node["children"]:
            return current
        return max(self._get_max_depth(child, current + 1) for child in node["children"])


# ============================================================================
# Custom Crawl Website Tool - Executor
# ============================================================================

class CrawlWebsiteExecutor(ToolExecutor[CrawlWebsiteAction, CrawlWebsiteObservation]):
    """Executor that performs the actual website crawling."""
    
    def __init__(self, output_dir: str = "."):
        self.output_dir = output_dir
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; SiteMapperBot/1.0; +https://github.com/openhands)"
        })
    
    def __call__(self, action: CrawlWebsiteAction, conversation=None) -> CrawlWebsiteObservation:
        """Execute the crawl action."""
        logger.info(f"🕷️ Starting crawl of {action.url} (max depth: {action.max_depth})")
        print(f"\n🕷️ Starting crawl of {action.url}")
        print(f"   Max depth: {action.max_depth}")
        print(f"   Mode: Internal links only\n")
        
        errors: list[str] = []
        
        # Parse and validate the starting URL
        try:
            parsed = urlparse(action.url)
            if not parsed.scheme:
                action.url = "https://" + action.url
                parsed = urlparse(action.url)
            domain = parsed.netloc
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        except Exception as e:
            errors.append(f"Invalid URL: {e}")
            return CrawlWebsiteObservation(errors=errors)
        
        print(f"🌐 Domain: {domain}")
        print(f"🔗 Base URL: {base_url}\n")
        
        # Data structures for BFS crawl
        visited: set[str] = set()
        url_data: dict[str, dict] = {}  # url -> {title, children_urls}
        
        # BFS queue: (url, depth, parent_url)
        queue: deque[tuple[str, int, str | None]] = deque()
        queue.append((action.url, 0, None))
        
        while queue:
            current_url, depth, parent_url = queue.popleft()
            
            # Normalize URL
            current_url = self._normalize_url(current_url)
            
            # Skip if already visited or exceeds depth
            if current_url in visited:
                continue
            if depth > action.max_depth:
                continue
            
            visited.add(current_url)
            
            # Fetch and parse the page
            print(f"📄 [{depth}] Fetching: {current_url[:80]}...")
            
            try:
                response = self.session.get(current_url, timeout=10, allow_redirects=True)
                response.raise_for_status()
                
                # Only process HTML pages
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    print(f"   ⏭️ Skipping non-HTML: {content_type}")
                    continue
                
                soup = BeautifulSoup(response.text, "html.parser")
                
                # Extract page title
                title_tag = soup.find("title")
                title = title_tag.get_text().strip() if title_tag else current_url
                
                # Extract all links
                links = self._extract_links(soup, current_url, base_url, domain)
                
                # Store page data
                url_data[current_url] = {
                    "url": current_url,
                    "title": title[:100],  # Limit title length
                    "children_urls": links,
                    "depth": depth,
                }
                
                print(f"   ✅ Found {len(links)} internal links")
                
                # Add children to queue (if not at max depth)
                if depth < action.max_depth:
                    for link in links:
                        if link not in visited:
                            queue.append((link, depth + 1, current_url))
                
                # Small delay to be polite
                time.sleep(0.2)
                
            except requests.exceptions.Timeout:
                errors.append(f"Timeout: {current_url}")
                print(f"   ⚠️ Timeout")
            except requests.exceptions.RequestException as e:
                errors.append(f"Error fetching {current_url}: {str(e)[:50]}")
                print(f"   ⚠️ Error: {str(e)[:50]}")
            except Exception as e:
                errors.append(f"Parse error {current_url}: {str(e)[:50]}")
                print(f"   ⚠️ Parse error: {str(e)[:50]}")
        
        print(f"\n📊 Crawl complete! Found {len(url_data)} pages")
        
        # Build the tree structure
        url_tree = self._build_tree(action.url, url_data)
        
        # Generate the visual sitemap
        output_file = os.path.join(self.output_dir, "sitemap.html")
        json_file = os.path.join(self.output_dir, "sitemap_data.json")
        
        self._generate_visual_sitemap(url_tree, domain, output_file)
        
        # Also save JSON data
        with open(json_file, "w") as f:
            json.dump(url_tree, f, indent=2)
        
        print(f"\n✨ Output files generated:")
        print(f"   📄 {output_file}")
        print(f"   📋 {json_file}")
        
        return CrawlWebsiteObservation(
            url_tree=url_tree,
            total_urls=len(url_data),
            domain=domain,
            errors=errors,
            output_file=output_file,
        )
    
    def _normalize_url(self, url: str) -> str:
        """Normalize a URL by removing fragments and trailing slashes."""
        parsed = urlparse(url)
        # Remove fragment
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        # Remove trailing slash (except for root)
        if normalized.endswith("/") and parsed.path != "/":
            normalized = normalized[:-1]
        return normalized
    
    def _extract_links(self, soup: BeautifulSoup, current_url: str, base_url: str, domain: str) -> list[str]:
        """Extract all internal links from a page."""
        links: set[str] = set()
        
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            
            # Skip empty, javascript, mailto, tel links
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            
            # Convert relative URLs to absolute
            full_url = urljoin(current_url, href)
            
            # Parse and validate
            parsed = urlparse(full_url)
            
            # Only keep internal links (same domain)
            if parsed.netloc != domain:
                continue
            
            # Skip non-http(s) schemes
            if parsed.scheme not in ("http", "https"):
                continue
            
            # Skip common non-page extensions
            path_lower = parsed.path.lower()
            skip_extensions = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", 
                            ".css", ".js", ".zip", ".doc", ".docx", ".xls", ".xlsx")
            if any(path_lower.endswith(ext) for ext in skip_extensions):
                continue
            
            # Normalize and add
            normalized = self._normalize_url(full_url)
            links.add(normalized)
        
        return sorted(links)
    
    def _build_tree(self, root_url: str, url_data: dict) -> dict:
        """Build a hierarchical tree structure from crawl data."""
        root_url = self._normalize_url(root_url)
        
        if root_url not in url_data:
            return {"name": root_url, "url": root_url, "children": []}
        
        root_info = url_data[root_url]
        
        def build_node(url: str, visited_in_tree: set) -> dict:
            if url in visited_in_tree or url not in url_data:
                return None
            
            visited_in_tree.add(url)
            info = url_data[url]
            
            node = {
                "name": info["title"],
                "url": url,
                "children": [],
            }
            
            # Add children (pages linked from this page)
            for child_url in info.get("children_urls", []):
                if child_url in url_data and child_url not in visited_in_tree:
                    child_node = build_node(child_url, visited_in_tree)
                    if child_node:
                        node["children"].append(child_node)
            
            return node
        
        return build_node(root_url, set())
    
    def _generate_visual_sitemap(self, tree: dict, domain: str, output_file: str):
        """Generate an interactive HTML sitemap visualization."""
        tree_json = json.dumps(tree)
        
        html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sitemap - {domain}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
        }}
        
        .header {{
            padding: 20px 40px;
            background: rgba(0, 0, 0, 0.3);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        h1 {{
            font-size: 1.8em;
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .subtitle {{
            color: #8892b0;
            margin-top: 5px;
        }}
        
        .stats {{
            display: flex;
            gap: 30px;
            margin-top: 15px;
        }}
        
        .stat {{
            background: rgba(255, 255, 255, 0.1);
            padding: 10px 20px;
            border-radius: 8px;
        }}
        
        .stat-value {{
            font-size: 1.5em;
            font-weight: bold;
            color: #00d4ff;
        }}
        
        .stat-label {{
            font-size: 0.85em;
            color: #8892b0;
        }}
        
        #tree-container {{
            width: 100%;
            height: calc(100vh - 140px);
            overflow: auto;
        }}
        
        .node circle {{
            fill: #00d4ff;
            stroke: #fff;
            stroke-width: 2px;
            cursor: pointer;
            transition: all 0.3s;
        }}
        
        .node circle:hover {{
            fill: #00ff88;
            transform: scale(1.2);
        }}
        
        .node text {{
            font-size: 12px;
            fill: #fff;
        }}
        
        .link {{
            fill: none;
            stroke: rgba(255, 255, 255, 0.3);
            stroke-width: 1.5px;
        }}
        
        .tooltip {{
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            border: 1px solid #00d4ff;
            padding: 10px 15px;
            border-radius: 8px;
            font-size: 12px;
            max-width: 400px;
            pointer-events: none;
            z-index: 1000;
        }}
        
        .tooltip-title {{
            color: #00d4ff;
            font-weight: bold;
            margin-bottom: 5px;
        }}
        
        .tooltip-url {{
            color: #8892b0;
            word-break: break-all;
        }}
        
        .controls {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            display: flex;
            gap: 10px;
        }}
        
        .control-btn {{
            background: rgba(0, 212, 255, 0.2);
            border: 1px solid #00d4ff;
            color: #00d4ff;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }}
        
        .control-btn:hover {{
            background: #00d4ff;
            color: #1a1a2e;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🗺️ Site Map: {domain}</h1>
        <p class="subtitle">Interactive visualization of website structure</p>
        <div class="stats">
            <div class="stat">
                <div class="stat-value" id="total-pages">0</div>
                <div class="stat-label">Total Pages</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="max-depth">0</div>
                <div class="stat-label">Max Depth</div>
            </div>
        </div>
    </div>
    
    <div id="tree-container"></div>
    
    <div class="tooltip" id="tooltip" style="display: none;"></div>
    
    <div class="controls">
        <button class="control-btn" onclick="expandAll()">Expand All</button>
        <button class="control-btn" onclick="collapseAll()">Collapse All</button>
        <button class="control-btn" onclick="resetZoom()">Reset Zoom</button>
    </div>
    
    <script>
        const treeData = {tree_json};
        
        // Count total nodes and max depth
        function countNodes(node) {{
            let count = 1;
            if (node.children) {{
                node.children.forEach(child => count += countNodes(child));
            }}
            return count;
        }}
        
        function getMaxDepth(node, depth = 0) {{
            if (!node.children || node.children.length === 0) return depth;
            return Math.max(...node.children.map(child => getMaxDepth(child, depth + 1)));
        }}
        
        document.getElementById('total-pages').textContent = countNodes(treeData);
        document.getElementById('max-depth').textContent = getMaxDepth(treeData);
        
        // Set up the tree visualization
        const container = document.getElementById('tree-container');
        const width = Math.max(container.clientWidth, 1200);
        const height = Math.max(container.clientHeight, 800);
        
        const svg = d3.select('#tree-container')
            .append('svg')
            .attr('width', width)
            .attr('height', height);
        
        const g = svg.append('g')
            .attr('transform', 'translate(100, 50)');
        
        // Enable zoom
        const zoom = d3.zoom()
            .scaleExtent([0.1, 3])
            .on('zoom', (event) => g.attr('transform', event.transform));
        
        svg.call(zoom);
        
        // Create tree layout
        const treeLayout = d3.tree()
            .size([height - 100, width - 200]);
        
        const root = d3.hierarchy(treeData);
        
        // Store original children for expand/collapse
        root.descendants().forEach(d => {{
            d._children = d.children;
        }});
        
        function update(source) {{
            const duration = 500;
            
            treeLayout(root);
            
            // Nodes
            const nodes = g.selectAll('.node')
                .data(root.descendants(), d => d.data.url);
            
            const nodeEnter = nodes.enter()
                .append('g')
                .attr('class', 'node')
                .attr('transform', d => `translate(${{source.y0 || d.y}},${{source.x0 || d.x}})`)
                .on('click', (event, d) => {{
                    if (d.children) {{
                        d._children = d.children;
                        d.children = null;
                    }} else {{
                        d.children = d._children;
                    }}
                    update(d);
                }})
                .on('mouseover', showTooltip)
                .on('mouseout', hideTooltip);
            
            nodeEnter.append('circle')
                .attr('r', 6);
            
            nodeEnter.append('text')
                .attr('dy', 4)
                .attr('x', d => d.children || d._children ? -12 : 12)
                .attr('text-anchor', d => d.children || d._children ? 'end' : 'start')
                .text(d => truncate(d.data.name, 30));
            
            const nodeUpdate = nodeEnter.merge(nodes);
            
            nodeUpdate.transition()
                .duration(duration)
                .attr('transform', d => `translate(${{d.y}},${{d.x}})`);
            
            nodeUpdate.select('circle')
                .attr('fill', d => d._children && !d.children ? '#f5576c' : '#00d4ff');
            
            nodes.exit()
                .transition()
                .duration(duration)
                .attr('transform', d => `translate(${{source.y}},${{source.x}})`)
                .remove();
            
            // Links
            const links = g.selectAll('.link')
                .data(root.links(), d => d.target.data.url);
            
            const linkEnter = links.enter()
                .insert('path', 'g')
                .attr('class', 'link')
                .attr('d', d3.linkHorizontal()
                    .x(d => source.y0 || d.y)
                    .y(d => source.x0 || d.x));
            
            linkEnter.merge(links)
                .transition()
                .duration(duration)
                .attr('d', d3.linkHorizontal()
                    .x(d => d.y)
                    .y(d => d.x));
            
            links.exit()
                .transition()
                .duration(duration)
                .attr('d', d3.linkHorizontal()
                    .x(d => source.y)
                    .y(d => source.x))
                .remove();
            
            // Store positions for transitions
            root.descendants().forEach(d => {{
                d.x0 = d.x;
                d.y0 = d.y;
            }});
        }}
        
        function truncate(str, n) {{
            return str.length > n ? str.substr(0, n - 1) + '...' : str;
        }}
        
        function showTooltip(event, d) {{
            const tooltip = document.getElementById('tooltip');
            tooltip.innerHTML = `
                <div class="tooltip-title">${{d.data.name}}</div>
                <div class="tooltip-url">${{d.data.url}}</div>
            `;
            tooltip.style.display = 'block';
            tooltip.style.left = (event.pageX + 15) + 'px';
            tooltip.style.top = (event.pageY - 10) + 'px';
        }}
        
        function hideTooltip() {{
            document.getElementById('tooltip').style.display = 'none';
        }}
        
        function expandAll() {{
            root.descendants().forEach(d => {{
                if (d._children) {{
                    d.children = d._children;
                }}
            }});
            update(root);
        }}
        
        function collapseAll() {{
            root.descendants().forEach(d => {{
                if (d.depth > 0 && d.children) {{
                    d._children = d.children;
                    d.children = null;
                }}
            }});
            update(root);
        }}
        
        function resetZoom() {{
            svg.transition()
                .duration(500)
                .call(zoom.transform, d3.zoomIdentity.translate(100, 50));
        }}
        
        // Initial render
        update(root);
        
        // Center the tree initially
        svg.call(zoom.transform, d3.zoomIdentity.translate(100, height / 4).scale(0.8));
    </script>
</body>
</html>'''
        
        with open(output_file, "w") as f:
            f.write(html_content)
        
        logger.info(f"Generated visual sitemap: {output_file}")


# ============================================================================
# Tool Definition
# ============================================================================

class CrawlWebsiteTool(ToolDefinition[CrawlWebsiteAction, CrawlWebsiteObservation]):
    """Tool for crawling websites and generating visual sitemaps."""
    
    name = "CrawlWebsiteTool"
    
    @classmethod
    def create(cls, conv_state, **kwargs) -> Sequence[ToolDefinition]:
        """Create the CrawlWebsiteTool instance."""
        output_dir = conv_state.workspace.working_dir if conv_state else "."
        executor = CrawlWebsiteExecutor(output_dir=output_dir)
        
        return [
            cls(
                description="""Crawl a website and generate a visual sitemap.

This tool:
- Crawls a website starting from the given URL
- Discovers all internal links (same domain only)
- Builds a hierarchical tree of the site structure
- Generates an interactive HTML visualization

Parameters:
- url: The starting URL to crawl (e.g., "https://example.com")
- max_depth: How deep to crawl (default: 2, max recommended: 3)

Output:
- sitemap.html: Interactive visual tree diagram
- sitemap_data.json: Raw URL tree data in JSON format
""",
                action_type=CrawlWebsiteAction,
                observation_type=CrawlWebsiteObservation,
                executor=executor,
            )
        ]


# Register the tool
register_tool(CrawlWebsiteTool.name, CrawlWebsiteTool.create)


# ============================================================================
# Main Agent Setup
# ============================================================================

def main():
    """Main entry point for the site mapper agent."""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Website Crawler Agent - Generate visual sitemaps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python site_mapper_agent.py https://example.com
  python site_mapper_agent.py https://docs.python.org --depth 2

Environment Variables:
  LLM_API_KEY      Your LLM API key (required)
  LLM_BASE_MODEL   Model to use (default: openhands/claude-sonnet-4-5-20250929)
"""
    )
    parser.add_argument("url", help="The website URL to crawl")
    parser.add_argument(
        "--depth", "-d",
        type=int,
        default=2,
        help="Maximum crawl depth (default: 2)"
    )
    
    args = parser.parse_args()
    
    # Validate environment
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("❌ Error: LLM_API_KEY environment variable is not set.")
        print("   Please set it with: export LLM_API_KEY='your-api-key'")
        sys.exit(1)
    
    model = os.getenv("LLM_BASE_MODEL", "openhands/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")
    
    print("=" * 60)
    print("🕷️  Website Crawler Agent")
    print("=" * 60)
    print(f"   URL: {args.url}")
    print(f"   Depth: {args.depth}")
    print(f"   Model: {model}")
    print("=" * 60)
    print()
    
    # Configure LLM
    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
    )
    
    # Set up tools
    tools = [
        Tool(name=CrawlWebsiteTool.name),
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]
    
    # Create agent
    agent = Agent(llm=llm, tools=tools)
    
    # Create conversation
    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)
    
    # Send the crawl request
    message = f"""Please crawl the website at {args.url} and create a visual sitemap.

Use the CrawlWebsiteTool with:
- url: {args.url}
- max_depth: {args.depth}

After crawling, let me know:
1. How many pages were discovered
2. The structure of the site
3. Where the output files were saved"""
    
    conversation.send_message(message)
    conversation.run()
    
    print("\n" + "=" * 60)
    print("✅ Agent finished!")
    print("=" * 60)


if __name__ == "__main__":
    main()
