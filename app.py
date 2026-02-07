import os
import csv
import io
from flask import Flask, render_template, request, Response, jsonify
from serpapi import GoogleSearch

app = Flask(__name__)

# Get API key from environment variable
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


def scrape_people_also_ask(keyword):
    """
    Get the 'People Also Ask' section from Google.nl using SerpAPI.
    Returns a list of dictionaries with question, answer, and source information.
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY environment variable not set")

    results = []

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

        # Extract "People Also Ask" questions
        related_questions = data.get("related_questions", [])

        for item in related_questions:
            question = item.get("question", "")
            snippet = item.get("snippet", "")
            title = item.get("title", "")
            link = item.get("link", "")

            if question:
                results.append({
                    "question": question,
                    "answer": snippet,
                    "source_title": title,
                    "source_url": link
                })

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
