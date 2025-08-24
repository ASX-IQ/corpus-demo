# AusIQ Corpus Demo

A Streamlit application for querying and analyzing ASX company announcements using OpenAI's GPT models and vector search.

## Prerequisites

- Python 3.8+
- OpenAI API key
- AWS credentials (for S3 access)

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/ASX-IQ/corpus-demo.git
   cd corpus-demo
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   ```

3. **Activate virtual environment**
   ```bash
   # On macOS/Linux
   source .venv/bin/activate
   
   # On Windows
   .venv\Scripts\activate
   ```

4. **Install requirements**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure Streamlit secrets**
   Create `.streamlit/secrets.toml` file with your credentials:
   ```toml
   OPENAI_API_KEY = "your-openai-api-key"
   ```

6. **Run the Streamlit application**
   ```bash
   streamlit run corpus.py
   ```

## File Structure

- `corpus.py` - Main Streamlit application
- `client_openai.py` - OpenAI client wrapper
- `conversation_manager.py` - Handles data retrieval and management
- `few_shot_prompts.py` - Prompt templates for AI responses
- `requirements.txt` - Python dependencies

