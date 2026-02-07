import os
import csv
import io
from flask import Flask, render_template, request, Response, jsonify
from serpapi import GoogleSearch

app = Flask(__name__)

# Get API key from environment variable
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


def is_relevant_question(question, keyword):
    """
    Determine if a question is relevant to the keyword.
    Returns True if relevant, False if less relevant (contains brand names, etc.)
    """
    question_lower = question.lower()
    keyword_lower = keyword.lower()

    # Common coffee machine brands (example)
    brand_names = [
        "jura", "delonghi", "de'longhi", "philips", "nespresso", "senseo",
        "dolce gusto", "siemens", "bosch", "melitta", "krups", "saeco",
        "moccamaster", "bialetti", "lavazza", "illy", "sage", "breville",
        # Car brands
        "bmw", "mercedes", "audi", "volkswagen", "toyota", "honda", "ford",
        "tesla", "volvo", "peugeot", "renault", "opel", "kia", "hyundai",
        # Tech brands
        "apple", "samsung", "sony", "lg", "google", "microsoft", "amazon",
        # Add more as needed
    ]

    # Check if question contains the keyword - likely relevant
    keyword_words = keyword_lower.split()
    contains_keyword = any(word in question_lower for word in keyword_words if len(word) > 2)

    # Check if question contains brand names - less relevant if brand not in keyword
    contains_brand = False
    for brand in brand_names:
        if brand in question_lower and brand not in keyword_lower:
            contains_brand = True
            break

    # Relevant if contains keyword and no unrelated brand
    if contains_keyword and not contains_brand:
        return True

    # Less relevant if contains brand not in keyword
    if contains_brand:
        return False

    # Default: if doesn't contain keyword, less relevant
    return contains_keyword


def parse_question(item, keyword=""):
    """Parse a single question item from SerpAPI response."""
    question = item.get("question", "")

    # Get answer from text_blocks (first paragraph)
    answer = ""
    text_blocks = item.get("text_blocks", [])
    for block in text_blocks:
        if block.get("type") == "paragraph" and block.get("snippet"):
            answer = block.get("snippet", "")
            break

    # Get source from references (first reference)
    source_title = ""
    source_url = ""
    references = item.get("references", [])
    if references:
        first_ref = references[0]
        source_title = first_ref.get("title", "")
        source_url = first_ref.get("link", "")

    # Determine relevance
    relevant = is_relevant_question(question, keyword) if keyword else True

    return {
        "question": question,
        "answer": answer,
        "source_title": source_title,
        "source_url": source_url,
        "next_page_token": item.get("next_page_token", ""),
        "relevant": relevant
    }


def scrape_people_also_ask(keyword, expand_questions=True, max_results=20):
    """
    Get the 'People Also Ask' section from Google.nl using SerpAPI.
    Returns a list of dictionaries with question, answer, and source information.

    Args:
        keyword: Search keyword
        expand_questions: If True, fetch additional questions using next_page_token
        max_results: Maximum number of results to return
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY environment variable not set")

    results = []
    seen_questions = set()

    try:
        # Configure search for Dutch Google
        params = {
            "engine": "google",
            "q": keyword,
            "google_domain": "google.nl",
            "gl": "nl",  # Country: Netherlands
            "hl": "nl",  # Language: Dutch
            "api_key": SERPAPI_KEY
        }

        search = GoogleSearch(params)
        data = search.get_dict()

        # Extract "People Also Ask" questions (SerpAPI uses different keys)
        related_questions = data.get("related_questions", [])

        # Also check "people_also_ask" key
        if not related_questions:
            related_questions = data.get("people_also_ask", [])

        # Collect tokens for expansion
        tokens_to_expand = []

        for item in related_questions:
            parsed = parse_question(item, keyword)
            if parsed["question"] and parsed["question"] not in seen_questions:
                seen_questions.add(parsed["question"])
                results.append({
                    "question": parsed["question"],
                    "answer": parsed["answer"],
                    "source_title": parsed["source_title"],
                    "source_url": parsed["source_url"],
                    "relevant": parsed["relevant"]
                })
                if parsed["next_page_token"]:
                    tokens_to_expand.append(parsed["next_page_token"])

        # Expand questions to get more results (uses additional API credits)
        if expand_questions and tokens_to_expand:
            for token in tokens_to_expand[:3]:  # Limit to 3 expansions to save credits
                if len(results) >= max_results:
                    break

                try:
                    expand_params = {
                        "engine": "google_related_questions",
                        "next_page_token": token,
                        "api_key": SERPAPI_KEY
                    }
                    expand_search = GoogleSearch(expand_params)
                    expand_data = expand_search.get_dict()

                    for item in expand_data.get("related_questions", []):
                        if len(results) >= max_results:
                            break
                        parsed = parse_question(item, keyword)
                        if parsed["question"] and parsed["question"] not in seen_questions:
                            seen_questions.add(parsed["question"])
                            results.append({
                                "question": parsed["question"],
                                "answer": parsed["answer"],
                                "source_title": parsed["source_title"],
                                "source_url": parsed["source_url"],
                                "relevant": parsed["relevant"]
                            })
                except Exception:
                    continue  # Skip failed expansions

    except Exception as e:
        print(f"Error during SerpAPI request: {e}")
        raise

    return results


@app.route("/", methods=["GET", "POST"])
def index():
    """Main page with keyword form and results display."""
    results = None
    keyword = ""
    error = None

    if request.method == "POST":
        keyword = request.form.get("keyword", "").strip()

        if not keyword:
            error = "Vul een zoekwoord in."
        elif not SERPAPI_KEY:
            error = "SERPAPI_KEY is niet geconfigureerd. Voeg deze toe aan de environment variables."
        else:
            try:
                results = scrape_people_also_ask(keyword)
                if not results:
                    error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord."
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template("index.html", results=results, keyword=keyword, error=error)


@app.route("/download-csv", methods=["POST"])
def download_csv():
    """Generate and download CSV file with the scraped results."""
    keyword = request.form.get("keyword", "").strip()

    if not keyword:
        return jsonify({"error": "Geen zoekwoord opgegeven"}), 400

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"}), 500

    try:
        results = scrape_people_also_ask(keyword)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not results:
        return jsonify({"error": "Geen resultaten gevonden"}), 404

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["question", "answer", "source_title", "source_url"])
    writer.writeheader()
    writer.writerows(results)

    # Create response with CSV file
    csv_content = output.getvalue()
    output.close()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=paa_{keyword.replace(' ', '_')}.csv"
        }
    )


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """API endpoint for programmatic access."""
    data = request.get_json()
    keyword = data.get("keyword", "").strip() if data else ""

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"}), 500

    try:
        results = scrape_people_also_ask(keyword)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "keyword": keyword,
        "results": results,
        "count": len(results)
    })


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "serpapi_configured": bool(SERPAPI_KEY)
    })


@app.route("/debug")
def debug():
    """Debug endpoint to see raw SerpAPI response."""
    keyword = request.args.get("q", "hypotheek")

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"})

    try:
        params = {
            "engine": "google",
            "q": keyword,
            "google_domain": "google.nl",
            "gl": "nl",
            "hl": "nl",
            "api_key": SERPAPI_KEY
        }

        search = GoogleSearch(params)
        data = search.get_dict()

        # Return relevant parts of the response
        return jsonify({
            "keyword": keyword,
            "has_related_questions": "related_questions" in data,
            "has_people_also_ask": "people_also_ask" in data,
            "related_questions_count": len(data.get("related_questions", [])),
            "people_also_ask_count": len(data.get("people_also_ask", [])),
            "related_questions": data.get("related_questions", []),
            "people_also_ask": data.get("people_also_ask", []),
            "available_keys": list(data.keys()),
            "error": data.get("error")
        })

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

