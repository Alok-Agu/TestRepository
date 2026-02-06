# Website Crawler Agent - Technical Implementation Plan

## Overview

A site mapping agent built with the OpenHands Software Agent SDK that discovers and maps all pages/URLs on a website, producing a visual sitemap with tree diagram showing page hierarchy.

## Requirements Summary

| Aspect | Specification |
|--------|---------------|
| **Agent Type** | Website Crawler for Site Mapping |
| **Output Format** | Visual sitemap (tree diagram/graph showing page hierarchy) |
| **Crawl Depth** | Shallow (1-2 levels) |
| **Domain Scope** | Internal only (stay within same domain) |
| **Input Method** | Command line argument |

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    Website Crawler Agent                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   CLI Input  │───▶│  Crawler     │───▶│  Visual Output   │  │
│  │   (URL)      │    │  Engine      │    │  Generator       │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
│                             │                     │              │
│                             ▼                     ▼              │
│                      ┌─────────────┐       ┌─────────────┐      │
│                      │  URL Queue  │       │  HTML Tree  │      │
│                      │  & Visited  │       │  Diagram    │      │
│                      └─────────────┘       └─────────────┘      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Input**: User provides URL via command line argument
2. **Crawling**: Agent fetches pages, extracts links, builds URL tree
3. **Filtering**: Only internal links within same domain are followed
4. **Depth Control**: Maximum 2 levels deep from start URL
5. **Output**: Visual HTML tree diagram saved to file

## Custom Tool Design

### CrawlWebsiteAction

```python
class CrawlWebsiteAction(Action):
    url: str          # Starting URL to crawl
    max_depth: int    # Maximum crawl depth (default: 2)
```

### CrawlWebsiteObservation

```python
class CrawlWebsiteObservation(Observation):
    url_tree: dict        # Hierarchical URL structure
    total_urls: int       # Total URLs discovered
    domain: str           # Crawled domain
    errors: list[str]     # Any crawl errors
```

### CrawlWebsiteExecutor

The executor will:
1. Parse the starting URL to extract the domain
2. Use `requests` and `BeautifulSoup` to fetch and parse HTML
3. Extract all `<a href>` links
4. Filter to keep only internal links (same domain)
5. Build a tree structure representing the site hierarchy
6. Respect max_depth limit (1-2 levels)

## Output Generation

### Visual Sitemap (HTML)

The agent will generate an interactive HTML file with:
- D3.js-based collapsible tree diagram
- Node labels showing page titles/URLs
- Clickable links to actual pages
- Color coding by depth level
- Statistics summary (total pages, depth levels)

### File Structure

```
output/
├── sitemap.html          # Interactive visual tree
└── sitemap_data.json     # Raw URL tree data
```

## Implementation Steps

1. **Create Custom Crawl Tool**
   - Define `CrawlWebsiteAction` with URL and depth parameters
   - Define `CrawlWebsiteObservation` with tree structure
   - Implement `CrawlWebsiteExecutor` with crawling logic

2. **Build URL Parser/Extractor**
   - Parse HTML pages using BeautifulSoup
   - Extract and normalize all anchor links
   - Filter to internal domain only
   - Handle relative vs absolute URLs

3. **Implement Depth-Limited BFS Crawl**
   - Use breadth-first search for systematic crawling
   - Track visited URLs to avoid duplicates
   - Respect max_depth parameter
   - Handle errors gracefully (404s, timeouts)

4. **Generate Visual Output**
   - Create D3.js tree visualization
   - Generate standalone HTML file
   - Include JSON data export

5. **CLI Integration**
   - Accept URL as command line argument
   - Optional depth parameter
   - Progress logging to terminal

## Dependencies

- `openhands-sdk` - Core agent SDK
- `openhands-tools` - Built-in tools
- `requests` - HTTP requests
- `beautifulsoup4` - HTML parsing
- `urllib` - URL manipulation (stdlib)

## Usage Example

```bash
# Basic usage
python output/site_mapper_agent.py https://example.com

# With custom depth
python output/site_mapper_agent.py https://example.com --depth 2
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LLM_API_KEY` | - | API key for LLM provider |
| `LLM_BASE_MODEL` | `openhands/claude-sonnet-4-5-20250929` | Model to use |
| `--depth` | 2 | Maximum crawl depth |

## Error Handling

- Invalid URL format → Clear error message
- Connection failures → Log and skip, continue crawling
- Rate limiting → Basic delay between requests
- Timeout → 10-second default timeout per page
