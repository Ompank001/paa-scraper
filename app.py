import os
import csv
import io
import re
from flask import Flask, render_template, request, Response, jsonify
from serpapi import GoogleSearch
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

app = Flask(__name__)

# Get API keys from environment variables
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Initialize OpenAI client
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Allowed domains for URL search
ALLOWED_DOMAINS = ["finerbrew.com", "de-koffiekompas.nl", "de-baardman.nl"]


def extract_page_info(url):
    """
    Extract H1 header and meta title from a URL.
    Returns a dictionary with h1, meta_title, and extracted keyword.
    """
    try:
        # Validate URL domain
        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.replace("www.", "")

        if domain not in ALLOWED_DOMAINS:
            raise ValueError(f"Domein niet toegestaan. Gebruik alleen: {', '.join(ALLOWED_DOMAINS)}")

        # Fetch the page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract H1
        h1_tag = soup.find("h1")
        h1_text = h1_tag.get_text(strip=True) if h1_tag else ""

        # Extract meta title
        title_tag = soup.find("title")
        meta_title = title_tag.get_text(strip=True) if title_tag else ""

        # Also try og:title as fallback
        og_title_tag = soup.find("meta", property="og:title")
        og_title = og_title_tag.get("content", "") if og_title_tag else ""

        # Determine main keyword
        keyword = determine_main_keyword(h1_text, meta_title, og_title)

        return {
            "h1": h1_text,
            "meta_title": meta_title,
            "og_title": og_title,
            "keyword": keyword,
            "url": url
        }

    except requests.RequestException as e:
        raise ValueError(f"Kon de URL niet ophalen: {str(e)}")
    except Exception as e:
        raise ValueError(f"Fout bij verwerken URL: {str(e)}")


def determine_main_keyword(h1, meta_title, og_title=""):
    """
    Determine the main keyword from H1 and meta title.
    Priority: H1 > meta title > og:title
    Cleans up common patterns like "| Site Name" from titles.
    """
    # Clean up meta title (remove site name after | or -)
    clean_title = meta_title
    for separator in [" | ", " - ", " – ", " — "]:
        if separator in clean_title:
            clean_title = clean_title.split(separator)[0].strip()

    # Priority: H1 if it's meaningful, otherwise cleaned title
    if h1 and len(h1) > 3:
        keyword = h1
    elif clean_title and len(clean_title) > 3:
        keyword = clean_title
    elif og_title and len(og_title) > 3:
        keyword = og_title
    else:
        keyword = meta_title

    # Clean up the keyword
    # Remove common prefixes/suffixes
    keyword = keyword.strip()

    # Remove numbering like "Top 10", "5 beste"
    keyword = re.sub(r"^(top\s*)?\d+\s+", "", keyword, flags=re.IGNORECASE)

    # Limit length (Google works better with shorter queries)
    words = keyword.split()
    if len(words) > 6:
        keyword = " ".join(words[:6])

    return keyword.strip()


def generate_answer(question, context=""):
    """
    Generate a well-formatted answer for a FAQ question using OpenAI.

    Rules:
    1. Answer starts with the question reformulated
    2. Maximum 75 words
    3. Informal "wij" form, informative and expert-like
    """
    if not openai_client:
        return None

    try:
        prompt = f"""Je bent een deskundige content schrijver voor een Nederlandse blog.
Beantwoord de volgende vraag volgens deze regels:

1. Begin het antwoord met de vraagstelling omgevormd tot een zin.
   Bijvoorbeeld: Vraag "Wat is de beste koffiemachine?" → Antwoord begint met "De beste koffiemachine is..."
   Vraag "Hoeveel kost een espressomachine?" → Antwoord begint met "Een espressomachine kost..."

2. Maximaal 75 woorden

3. Schrijf in de wij-vorm (bijv. "Wij raden aan...", "Volgens ons...")

4. Houd het informatief en deskundig, maar niet te formeel

5. Geef praktisch en nuttig advies

Vraag: {question}

{f"Context/achtergrond informatie: {context}" if context else ""}

Antwoord:"""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Je bent een deskundige Nederlandse content schrijver die informatieve, beknopte antwoorden geeft."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )

        answer = response.choices[0].message.content.strip()

        # Ensure answer doesn't exceed 75 words
        words = answer.split()
        if len(words) > 75:
            answer = " ".join(words[:75]) + "..."

        return answer

    except Exception as e:
        print(f"Error generating answer: {e}")
        return None


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


def parse_question(item, keyword="", generate_ai_answer=False):
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

    # Generate AI answer if requested
    generated_answer = None
    if generate_ai_answer and question:
        generated_answer = generate_answer(question, answer)

    return {
        "question": question,
        "answer": answer,
        "generated_answer": generated_answer,
        "source_title": source_title,
        "source_url": source_url,
        "next_page_token": item.get("next_page_token", ""),
        "relevant": relevant
    }


def scrape_people_also_ask(keyword, expand_questions=True, max_results=20, generate_answers=False):
    """
    Get the 'People Also Ask' section from Google.nl using SerpAPI.
    Returns a list of dictionaries with question, answer, and source information.

    Args:
        keyword: Search keyword
        expand_questions: If True, fetch additional questions using next_page_token
        max_results: Maximum number of results to return
        generate_answers: If True, generate AI answers for each question
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
            parsed = parse_question(item, keyword, generate_ai_answer=generate_answers)
            if parsed["question"] and parsed["question"] not in seen_questions:
                seen_questions.add(parsed["question"])
                results.append({
                    "question": parsed["question"],
                    "answer": parsed["answer"],
                    "generated_answer": parsed["generated_answer"],
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
                        parsed = parse_question(item, keyword, generate_ai_answer=generate_answers)
                        if parsed["question"] and parsed["question"] not in seen_questions:
                            seen_questions.add(parsed["question"])
                            results.append({
                                "question": parsed["question"],
                                "answer": parsed["answer"],
                                "generated_answer": parsed["generated_answer"],
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
    generate_answers = False

    if request.method == "POST":
        keyword = request.form.get("keyword", "").strip()
        generate_answers = request.form.get("generate_answers") == "on"

        if not keyword:
            error = "Vul een zoekwoord in."
        elif not SERPAPI_KEY:
            error = "SERPAPI_KEY is niet geconfigureerd. Voeg deze toe aan de environment variables."
        elif generate_answers and not OPENAI_API_KEY:
            error = "OPENAI_API_KEY is niet geconfigureerd. Voeg deze toe om antwoorden te genereren."
        else:
            try:
                results = scrape_people_also_ask(keyword, generate_answers=generate_answers)
                if not results:
                    error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord."
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template(
        "index.html",
        results=results,
        keyword=keyword,
        error=error,
        active_tab="keyword",
        generate_answers=generate_answers,
        openai_configured=bool(OPENAI_API_KEY)
    )


@app.route("/url", methods=["GET", "POST"])
def url_search():
    """URL-based search: extract keyword from page and search PAA."""
    results = None
    url = ""
    keyword = ""
    page_info = None
    error = None
    generate_answers = False

    if request.method == "POST":
        url = request.form.get("url", "").strip()
        generate_answers = request.form.get("generate_answers") == "on"

        if not url:
            error = "Vul een URL in."
        elif not SERPAPI_KEY:
            error = "SERPAPI_KEY is niet geconfigureerd."
        elif generate_answers and not OPENAI_API_KEY:
            error = "OPENAI_API_KEY is niet geconfigureerd. Voeg deze toe om antwoorden te genereren."
        else:
            # Add https:// if missing
            if not url.startswith("http"):
                url = "https://" + url

            try:
                # Extract page info
                page_info = extract_page_info(url)
                keyword = page_info["keyword"]

                if not keyword:
                    error = "Kon geen zoekwoord bepalen uit de pagina."
                else:
                    # Search PAA with extracted keyword
                    results = scrape_people_also_ask(keyword, generate_answers=generate_answers)
                    if not results:
                        error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord."

            except ValueError as e:
                error = str(e)
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template(
        "index.html",
        results=results,
        keyword=keyword,
        url=url,
        page_info=page_info,
        error=error,
        active_tab="url",
        generate_answers=generate_answers,
        openai_configured=bool(OPENAI_API_KEY)
    )


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

    # Check if any result has generated_answer
    has_generated = any(r.get("generated_answer") for r in results)

    if has_generated:
        fieldnames = ["question", "generated_answer", "answer", "source_title", "source_url"]
    else:
        fieldnames = ["question", "answer", "source_title", "source_url"]

    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
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

