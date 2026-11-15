"""Sample API responses shaped like each provider's documented format, for offline tests."""

PERPLEXITY = {
    "id": "resp_123",
    "model": "perplexity/sonar",
    "status": "completed",
    "output": [
        {
            "type": "search_results",
            "queries": ["best coworking bangalore"],
            "results": [
                {"id": 1, "url": "https://www.example.com/best-coworking", "title": "Top 10 Coworking"},
                {"id": 2, "url": "https://blog.other.in/wework-review", "title": "WeWork review"},
                {"id": 3, "url": "https://uncited.com/page", "title": "Not cited"},
                {"id": 4, "url": "https://www.example.com/best-coworking", "title": "Duplicate URL"},
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Top picks are WeWork [web:1] and Awfis [web:1, web:2]. Also myHQ [2].",
                    "annotations": [],
                }
            ],
        },
    ],
    "usage": {
        "input_tokens": 3200,
        "output_tokens": 450,
        "cost": {"total_cost": 0.0049, "currency": "USD"},
        "tool_calls_details": {"search_web": {"invocation": 1}},
    },
}

OPENAI = {
    "model": "gpt-6-sol",
    "status": "completed",
    "output": [
        {
            "type": "web_search_call",
            "status": "completed",
            "action": {
                "type": "search",
                "query": "coworking bangalore",
                "sources": [{"type": "url", "url": "https://www.awfis.com/"}, {"type": "url", "url": "https://retrieved-only.com/x"}],
            },
        },
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "Awfis is popular. myHQ by ANAROCK lets you book day passes.",
                    "annotations": [
                        {"type": "url_citation", "url": "https://www.awfis.com/?utm_source=openai", "title": "Awfis",
                         "start_index": 0, "end_index": 17},
                        {"type": "url_citation", "url": "https://myhq.in/blog?utm_source=openai", "title": "myHQ",
                         "start_index": 18, "end_index": 60},
                    ],
                }
            ],
        },
    ],
    "usage": {"input_tokens": 5000, "output_tokens": 800},
}

CLAUDE = {
    "model": "claude-opus-5-5",
    "stop_reason": "end_turn",
    "content": [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "coworking"}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {"type": "web_search_result", "url": "https://www.smartworks.in/a", "title": "Smartworks"},
                {"type": "web_search_result", "url": "https://reddit.com/r/bangalore/x", "title": "Reddit thread"},
            ],
        },
        {"type": "text", "text": "Good options include "},
        {
            "type": "text",
            "text": "Smartworks for large teams",
            "citations": [{"type": "web_search_result_location", "url": "https://reddit.com/r/bangalore/x",
                           "title": "Reddit thread", "cited_text": "..."}],
        },
        {"type": "text", "text": " and IndiQube."},
    ],
    "usage": {"input_tokens": 12000, "output_tokens": 600, "cache_read_input_tokens": 0,
              "server_tool_use": {"web_search_requests": 2}},
}

GEMINI = {
    "candidates": [
        {
            "content": {"parts": [{"text": "thinking...", "thought": True},
                                  {"text": "BHIVE and WeWork are well rated. myHQ is a booking platform."}]},
            "groundingMetadata": {
                "webSearchQueries": ["coworking bangalore", "best coworking"],
                "groundingChunks": [
                    {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc", "title": "bhiveworkspace.com"}},
                    {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/def", "title": "Some article"}},
                ],
                "groundingSupports": [{"segment": {"startIndex": 0, "endIndex": 30}, "groundingChunkIndices": [0]}],
            },
        }
    ],
    "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 300, "thoughtsTokenCount": 100,
                      "toolUsePromptTokenCount": 900},
    "modelVersion": "gemini-3.8-flash",
}
