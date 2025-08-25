import os
import time
import streamlit as st
from datetime import datetime, timedelta
import uuid
import hashlib
from client_openai import ClientOpenAI
from conversation_manager import ConversationManager

def create_query_hash(ticker, date_from, date_to, announcement_types, price_sensitive):
    """Create unique hash for the query"""
    query_string = f"{ticker}_{date_from}_{date_to}_{sorted(announcement_types or [])}_{price_sensitive}"
    return hashlib.md5(query_string.encode()).hexdigest()[:8]

def needs_vs_update():
    """Check if we need to create new vs and upload the documents"""
    if not st.session_state.ticker:
        return False, 'No ticker selected'

    current_hash = create_query_hash(
        st.session_state.ticker,
        st.session_state.date_from,
        st.session_state.date_to,
        st.session_state.selected_reports,
        conversation_manager.price_sensitive
    )

    # Compare the hashed query settings
    if current_hash != st.session_state.current_query_hash:
        prev_hash = st.session_state.current_query_hash
        st.session_state.current_query_hash = current_hash

        # New sessions
        if prev_hash is None:
            return True, 'Initial setup'
        # Changed query
        else:
            return True, 'Query changed'

    return False, 'No changes'

def get_new_docs():
    """Compare the existing docs and decide if need to find the new ones"""
    if not st.session_state.ticker:
        return [], {}

    new_files, new_types_counted = conversation_manager.get_s3_keys()

    prev_files = st.session_state.get('loaded_documents', [])
    increment_files = [file for file in new_files if file not in prev_files]
    return increment_files, new_types_counted, new_files

def chat_placeholder_text(ticker, chat_mode):
    text = ''

    if ticker and chat_mode:
        text = "Ask a question about ASX ticker you want to research"
    elif ticker and not chat_mode:
        text = 'Please select chat mode'
    elif not ticker and chat_mode:
        text = 'Please select the company first'
    elif not ticker and not chat_mode:
        text = 'Please select the company and chat mode'

    return text

def generate_prompts_buttons(input_disabled):
    defined_prompts = [
        "Details of main asset",
        "Summary of financial situation",
        "Details of last capital raise",
        "Most recent announcement",
        "Company's next announcement",
        "Progress in last 6 months",
    ]

    btn_cols = st.columns(len(defined_prompts))

    for i, prompt_text in enumerate(defined_prompts):
        with btn_cols[i]:
            if st.button(label=prompt_text, key=f"prompt_btn_{i}", disabled=input_disabled):
                return prompt_text
    return None

@st.cache_data(ttl=1200, show_spinner='Fetching data...')
def get_company_data():
    return conversation_manager.get_companies_data()

@st.cache_data(ttl=1200, show_spinner='Fetching data...')
def get_or_create_vector_store(ticker):
    return client.create_vs(ticker)


# Load secrets
AWS_ID = st.secrets["access_key_id"]
AWS_KEY = st.secrets["secret_access_key"]
AWS_REGION = st.secrets["region"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
AVATAR = 'AusIQ logo.jpg'
first_message = 'Hello! How can AusIQ Company Corpus help you today?"'

# Initialize session state variables
if 'openai_client' not in st.session_state:
    st.session_state.openai_client = ClientOpenAI(OPENAI_API_KEY)
client = st.session_state.openai_client

if 'conversation_manager' not in st.session_state:
    st.session_state.conversation_manager = ConversationManager(AWS_ID, AWS_KEY, AWS_REGION)
conversation_manager = st.session_state.conversation_manager

# Chats displayed in the chat window
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "avatar": AVATAR, "content": first_message}
    ]

if 'messages_history' not in st.session_state:
    st.session_state.messages_history = []

if 'prompt' not in st.session_state:
    st.session_state.prompt = []

if "current_prompt" not in st.session_state:
    st.session_state.current_prompt = None

if "ticker" not in st.session_state:
    st.session_state.ticker = None

if "vector_store_id" not in st.session_state:
    st.session_state.vector_store_id = None

if "kb_ready" not in st.session_state:
    st.session_state.kb_ready = False

if "selected_reports" not in st.session_state:
    st.session_state.selected_reports = None

if 'date_range' not in st.session_state:
    st.session_state.date_range = None

if 'date_from' not in st.session_state:
    st.session_state.date_from = None

if 'date_to' not in st.session_state:
    st.session_state.date_to = None

if "num_results" not in st.session_state:
    st.session_state.num_results = 10

if "files_keys" not in st.session_state:
    st.session_state.files_keys = None

if "search_results_content" not in st.session_state:
    st.session_state.search_results_content = {}

if 'types_counted' not in st.session_state:
    st.session_state.types_counted = {}

# Track loaded documents and query hash
if 'loaded_documents' not in st.session_state:
    st.session_state.loaded_documents = []

if 'current_query_hash' not in st.session_state:
    st.session_state.current_query_hash = None

# Track ticker-specific vector stores
if 'ticker_vector_stores' not in st.session_state:
    st.session_state.ticker_vector_stores = {}

if 'ticker_loaded_documents' not in st.session_state:
    st.session_state.ticker_loaded_documents = {}

if 'ticker_query_hashes' not in st.session_state:
    st.session_state.ticker_query_hashes = {}

st.set_page_config(layout="wide")

st.header('AusIQ Corpus', divider='red', width='content')

# Columns split
col1, col2, col3 = st.columns([1,4,1])

# Left column - Query settings
with col1:

    companies_dict = get_company_data()

    selected_company = st.selectbox(
        label="Select the company",
        key="company_selectbox",
        options=[None] + [f"{key} ({value})" for key, value in companies_dict.items()],
        help="Select which company would you like to chat about",
        format_func=lambda x: "Select an option" if x is None else x
    )

    if selected_company:
        company_name = selected_company.split(" (")[0]
        ticker = companies_dict[company_name]

        # Add to conversation manager
        conversation_manager.ticker = ticker

        # Add to client for the system prompt
        client.update_company_info(company_name, ticker)

        # Handle ticker changes with vector store caching
        if conversation_manager.ticker != st.session_state.ticker:
            # Save current state before switching
            if st.session_state.ticker:
                st.session_state.ticker_loaded_documents[
                    st.session_state.ticker] = st.session_state.loaded_documents.copy()
                st.session_state.ticker_query_hashes[st.session_state.ticker] = st.session_state.current_query_hash

            # Switch to new ticker
            st.session_state.ticker = conversation_manager.ticker

            if st.session_state.ticker:
                # Check if we have a cached vector store for this ticker
                if st.session_state.ticker in st.session_state.ticker_vector_stores:
                    st.session_state.vector_store_id = st.session_state.ticker_vector_stores[st.session_state.ticker]
                    st.session_state.kb_ready = True  # Assume ready since it's cached

                    # Restore loaded documents and query hash for this ticker
                    st.session_state.loaded_documents = st.session_state.ticker_loaded_documents.get(
                        st.session_state.ticker, [])
                    st.session_state.current_query_hash = st.session_state.ticker_query_hashes.get(
                        st.session_state.ticker, None)
                else:
                    # Create new vector store for this ticker
                    vector_store_id = get_or_create_vector_store(st.session_state.ticker)
                    st.session_state.vector_store_id = vector_store_id
                    st.session_state.ticker_vector_stores[st.session_state.ticker] = vector_store_id
                    st.session_state.kb_ready = False
                    st.session_state.loaded_documents = []  # Reset for new ticker
                    st.session_state.current_query_hash = None  # Reset query hash for new ticker

            # Clear chat history when ticker changes
            st.session_state.messages = [{"role": "assistant", "avatar": AVATAR, "content": first_message}]
            st.session_state.search_results_content = {}
            client.clear_chat_history()

        client.vs_id = st.session_state.vector_store_id
        # st.session_state.user_data['vector_store']['vs_id'] = st.session_state.vector_store_id

    # Available report options
    report_options = [
        "Cashflow Reports",
        "Mining studies/resources",
        "Placements",
        "Shares 3B's, 2A's",
        "Presentations",
    ]

    conversation_manager.announcement_types = st.multiselect(
        label="Select announcement types",
        options=[None] + [opt for opt in report_options],
        default=None,
        format_func=lambda x: "All reports" if x is None else x,
        help="Select the specific announcement types or leave empty or all announcements"
    )

    st.session_state.selected_reports = conversation_manager.announcement_types

    conversation_manager.price_sensitive = st.checkbox(
        "Price Sensitive (only)",
        value=False,
        help="Select the box for price sensitive announcements"
    )

    # Dates for the query
    date_180_days = datetime.today() - timedelta(days=180)
    date_1_year = datetime.today() - timedelta(days=365)
    date_today = datetime.today()

    # Date inputs
    date_from = st.date_input(
        "Announcement publication dates from",
        value=date_180_days,
        min_value=date_1_year,
        max_value=date_today,
        help="Select the starting date for the announcements selection"
    )

    date_to = st.date_input(
        "Announcement publication dates to",
        value=date_today,
        min_value=date_1_year,
        max_value=date_today,
        help="Select the ending date for the announcements selection"
    )

    # Update the date range (days)
    st.session_state.date_range = (date_to - date_from).days
    st.session_state.date_from = date_from
    st.session_state.date_to = date_to
    st.write(f'Date range (days): {st.session_state.date_range}')

    # Add information for the user
    with st.expander('Query information', expanded=False):
        chat_mode = st.pills(
            label="Chat mode",
            options=['Generate', 'Search'],
            selection_mode='single',
            key="search_generate",
            default="Generate",
            help="Select between LLM generation and document search",
            width="stretch"
        )
        st.slider(
            label="Max number of citations",
            key="num_results",
            min_value=1,
            max_value=20,
            step=1,
            help="Select how many cited sources to provide for you question"
        )

        if st.session_state.types_counted:
            for key, value in st.session_state.types_counted.items():
                st.write(f"{key} | Count: {value}")

    # Update the conv manager
    conversation_manager.date_from = st.session_state.date_from
    conversation_manager.date_to = st.session_state.date_to


# Chat
with col2:

    # Chat container
    with st.container(height=550, border=True):
        # Display user message

        for message in st.session_state.messages:
            with st.chat_message(message["role"], avatar=message.get("avatar", None)):
                st.write(message["content"])

        # Generate assistant response
        if st.session_state.get("generate_response") and st.session_state.current_prompt:
            st.session_state.messages.append({
                "role": "assistant",
                "avatar": AVATAR,
                "content": ""
            })

            # Summarize the last QA
            if len(st.session_state.messages_history) >= 2:
                # Pass the current Q&A pair for summarization
                current_pair = st.session_state.messages_history[-2:]  # Last user question + assistant response

            # Stream
            with st.chat_message("assistant", avatar=AVATAR):
                placeholder = st.empty()
                full_response = ""

                # Stream the response
                for chunk in client.generate(prompt=st.session_state.current_prompt, max_results=st.session_state.num_results):
                    full_response += chunk

                    # Stream the response to placeholder
                    placeholder.write(full_response + "▌")  # Streaming cursor

                # Final message without cursor
                placeholder.write(full_response)

            user_question = st.session_state.current_prompt
            assistant_response = full_response

            # Summarize and add to history
            client.summarize_history(user_question, assistant_response)

            # Grab the full message
            st.session_state.messages[-1]["content"] = full_response

            st.session_state.messages_history.append(full_response)

            # Add references if any exist
            if client.annotations:
                st.session_state.messages.append({
                    "role": "assistant",
                    "avatar": AVATAR,
                    "content": client.annotations
                })

            # Reset flags
            st.session_state.generate_response = False
            st.session_state.current_prompt = None

            # Reset to display the final message
            st.rerun()

    # Chat input
    input_disabled = not conversation_manager.ticker or not st.session_state.search_generate
    placeholder_text =  chat_placeholder_text(st.session_state.ticker, st.session_state.search_generate)

    # Predefined prompts
    selected_prompt = generate_prompts_buttons(input_disabled)

    # Collect the user input
    st.session_state.prompt = selected_prompt if selected_prompt else st.chat_input(placeholder_text, disabled=input_disabled)

    # Message sent
    if st.session_state.prompt:
        # Add message in chat window
        st.session_state.messages.append({"role": "user", "content": st.session_state.prompt})
        st.session_state.messages_history.append(st.session_state.prompt)

        # Change current prompt
        st.session_state.current_prompt = st.session_state.prompt

        # Check if vector store needs updating
        needs_update, update_reason = needs_vs_update()

        # The query changed
        if needs_update:
            # Get incremental documents
            incremental_pdfs, new_types_counted, all_pdfs = get_new_docs()

            if incremental_pdfs: # New query settings
                spinner_text = f"Preparing {len(incremental_pdfs)} new documents ({update_reason})..."
            else: # Initial upload
                spinner_text = f"Preparing documents ({update_reason})..."

            with st.spinner(spinner_text, show_time=True):
                if incremental_pdfs:
                    # Add only new documents to existing vector store
                    payload = conversation_manager.create_payload(keys=incremental_pdfs, vs_id=st.session_state.vector_store_id)
                    upload = conversation_manager.lambda_s3_files_upload(payload=payload)

                    if upload == -1:
                        st.toast(icon='⚠️', body='Error uploading new files')
                        time.sleep(2)

                    st.session_state.loaded_documents.extend(incremental_pdfs)
                    # Also update the ticker-specific cache
                    if st.session_state.ticker:
                        st.session_state.ticker_loaded_documents[
                            st.session_state.ticker] = st.session_state.loaded_documents.copy()
                elif all_pdfs:
                    # This handles case where we have a cached vector store but need to load documents
                    payload = conversation_manager.create_payload(keys=all_pdfs, vs_id=st.session_state.vector_store_id)
                    upload = conversation_manager.lambda_s3_files_upload(payload=payload)

                    if upload == -1:
                        st.toast(icon='⚠️', body='Error uploading new files')

                    st.session_state.loaded_documents = all_pdfs
                    # Also update the ticker-specific cache
                    if st.session_state.ticker:
                        st.session_state.ticker_loaded_documents[
                            st.session_state.ticker] = st.session_state.loaded_documents.copy()

                # Update session state
                st.session_state.types_counted = new_types_counted
                st.session_state.kb_ready = True

                # Save the current query hash to ticker cache
                if st.session_state.ticker:
                    st.session_state.ticker_query_hashes[st.session_state.ticker] = st.session_state.current_query_hash

        # Generate/Search
        if st.session_state.search_generate == 'Generate':
            st.session_state.generate_response = True
            st.session_state.prompt = None
        elif st.session_state.search_generate == 'Search':
            st.session_state.generate_response = False

            # Search mode
            search_results = client.search(
                prompt=st.session_state.prompt,
                max_results=st.session_state.num_results
            )

            st.session_state.prompt = None

            # Generate a unique ID for this search result
            result_id = str(uuid.uuid4())

            # Prepare the search results content
            search_results_content = ""

            for result in search_results.data:
                filename = result.filename
                file_id = filename.split('_', 2)[0].rsplit('.', 1)[0]
                url = f"https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{file_id}"
                score = round(result.score, 2)
                content = result.content[0].text
                preview = content[:300] + "..." if len(content) > 300 else content
                search_results_content += f"\n📄 **File:** {url} \n\n 🔢**Relevance Score:** `{score}` \n\n✏️**Preview:**\n\n{preview} \n\n"

            # Store the search results
            st.session_state.search_results_content[result_id] = search_results_content

            # Add the result to messages
            st.session_state.messages.append({
                "role": "assistant",
                "avatar": AVATAR,
                "content": f"Here are the search results: \n {search_results_content}",
                "id": result_id
            })
        else:
            st.session_state.generate_response = True
            st.session_state.prompt = None
            st.toast(icon='⚠️', body='Please select chat mode.')
            time.sleep(2)

        st.rerun()


    with st.expander('Chat summarized (Admin only)', expanded=False):
        st.write(client.summary_history)
