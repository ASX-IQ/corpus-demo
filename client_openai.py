import re
import time
from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    NotFoundError,
    InternalServerError,
    BadRequestError
  )

from few_shot_prompts import build_system_prompt

class ClientOpenAI:
    """
    Class for organized usage of OpenAI API
    Vector Store ID is associated with an object during the session
    """
    def __init__(self, openai_api_key):
        """
        Initialize OpenAI client with default configuration and chat history management.
        
        Sets up the OpenAI client, initializes model parameters, confidence scoring,
        and prepares chat history tracking for conversation continuity.
        
        Args:
            openai_api_key (str): OpenAI API authentication key
        """
        self.client = OpenAI(api_key=openai_api_key)
        self.vs_id = None
        self.company = ''
        self.ticker = ''
        self.confidence_score = 0.7
        self.max_tokens = 1500
        self.annotations = []
        self.model = 'gpt-5-mini'
        self.base_system_prompt = build_system_prompt(self.confidence_score, self.company, self.ticker)
        self.system_prompt_with_history = ''
        self.chat_history = []
        self.summary_history = []

    def create_vs(self, selected_ticker):
        """
        Create vector store for chosen ticker to upload documents to.
        """
        vs_name = f"{selected_ticker}_vs"

        vector_store = self.client.vector_stores.create(
            name=vs_name,
            expires_after={"anchor": "last_active_at", "days": 1}
        )

        self.vs_id = vector_store.id

        return vector_store.id

    # noinspection PyTypeChecker
    def generate(self, prompt, max_results):
        """
        Generates documents using OpenAI API.
        The response is streamed back into streamlit
        Annotations are captured by event 'response.output_text.annotation.added' and handled separately in _process_annotations
        Each call dynamically rebuilds the system prompt with the summarized chat history.
        """

        # Start with base chat history
        self.system_prompt_with_history = self.base_system_prompt

        # Add chat history if exists
        if self.chat_history:
            self.system_prompt_with_history += "\n\n## Conversation History Summary:\n"
            self.system_prompt_with_history += "\n".join(f"- {summary}" for summary in self.chat_history)

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                response = self.client.responses.create(
                    model=self.model,
                    input=prompt,
                    instructions=self.system_prompt_with_history,
                    stream=True,
                    # max_output_tokens=self.max_tokens,
                    # reasoning={"effort": "medium"},
                    text={"verbosity": "low"},
                    tools=[
                        {
                            "type": "file_search",
                            "max_num_results": max_results,
                            "vector_store_ids": [self.vs_id]
                        },
                        {
                            "type": "web_search_preview"
                        }
                    ]
                )

                annotations = []

                # Handle annotations
                for event in response:
                    match event.type:
                        case 'response.output_text.delta':
                            if hasattr(event, 'delta'):
                                # Clean the chunk of text from any formatting
                                text_chunk = self._clean_response(event.delta)

                                # Delay between chunks
                                time.sleep(0.04)

                                yield text_chunk

                        case 'response.output_text.annotation.added':
                            if hasattr(event, 'annotation'):
                                annotations.append(event.annotation)

                        case 'response.failed':
                            error_details = getattr(event, 'error', {})
                            print(f"🔍 Debug: Response failed: {error_details}")
                            yield f"Response failed. Please try again."
                            return

                        case 'response.incomplete':
                            yield "Response was incomplete. Please try again."

                        case 'response.cancelled':
                            print(event)
                            yield "Response was cancelled"
                            return

                        case 'response.completed':
                            pass

                # Handle annotations
                self.annotations = self._process_annotations(annotations)
                return # Success

            except NotFoundError as e:
                error_msg = "Resource not found. Please check model name or vector store ID."
                print(f"Not Found Error: {e}")
                yield error_msg
                return  # Don't retry not found errors
            except BadRequestError as e:
                error_msg = f"Invalid request: {e.response.json().get('error', {}).get('message', str(e))}"
                print(f"Bad Request Error: {e}")
                yield error_msg
                return  # Don't retry bad requests
            except InternalServerError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"Server error. Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    error_msg = "Server error. Please try again later."
                    print(f"Internal Server Error: {e}")
                    yield error_msg
                    return
            except APIConnectionError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"Connection error. Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    error_msg = "Connection failed. Please check your internet connection."
                    print(f"Connection Error: {e}")
                    print(f"Underlying cause: {e.__cause__}")
                    yield error_msg
                    return
            except APIStatusError as e:
                error_msg = f"API error (status {e.status_code}): {e.response.json().get('error', {}).get('message', str(e))}"
                print(f"API Status Error: {e}")
                print(f"Status Code: {e.status_code}")
                print(f"Response: {e.response}")
                yield error_msg
                return  # Don't retry most API status errors
            except Exception as e:
                # Catch-all for unexpected errors
                error_msg = f"Unexpected error: {str(e)}"
                print(f"Unexpected Error: {e}")
                yield error_msg
                return

    def _process_annotations(self, annotations):
        """
        Process annotations provided by OpenAI API, splits either into File Citations or URLs
        """
        file_citations = []
        url_citations = []
        reference_content = ""

        if annotations:
            for ann in annotations:
                # Handle both dictionary and object formats
                if isinstance(ann, dict):
                    if ann.get('type') == 'file_citation' and 'filename' in ann:
                        file_citations.append(ann)
                    elif ann.get('type') == 'web_citation' and 'url' in ann:
                        url_citations.append(ann)
                else:
                    if hasattr(ann, 'filename'):  # File search annotation
                        file_citations.append(ann)
                    elif hasattr(ann, 'url'):  # Web search annotation
                        url_citations.append(ann)

        # Process file citations
        if file_citations:
            unique_file_citations = {}
            for ann in file_citations:
                # Handle both dictionary and object formats
                filename = ann.get('filename') if isinstance(ann, dict) else ann.filename
                if filename not in unique_file_citations:
                    unique_file_citations[filename] = ann

            # Form file URLs
            file_urls = []
            for ann in unique_file_citations.values():
                filename = ann.get('filename') if isinstance(ann, dict) else ann.filename
                try:
                    file_id_extracted = filename.split('_', 2)[2].rsplit('.', 1)[0]
                    url = f"https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{file_id_extracted}"
                    file_urls.append(url)
                except IndexError:
                    continue

            if file_urls:
                reference_content = "🔗 Referenced Files:\n"
                for url in file_urls:
                    reference_content += f"- [File Link]({url})\n"

        # Process web citations
        if url_citations:
            web_reference_content = "🌐 Web Sources:\n"
            for ann in url_citations:
                # Handle both dictionary and object formats
                title = ann.get('title', 'Web Source') if isinstance(ann, dict) else getattr(ann, 'title', 'Web Source')
                url = ann.get('url') if isinstance(ann, dict) else ann.url
                web_reference_content += f"- [{title}]({url})\n"

            if file_citations:
                reference_content += "\n" + web_reference_content
            else:
                reference_content = web_reference_content

        return reference_content

    def search(self, prompt, max_results):
        """
        Search vector store for relevant documents using semantic similarity.
        
        Performs vector search on the configured vector store and cleans the content
        by removing markdown headers and bold formatting from results.
        
        Args:
            prompt (str): Search query text
            max_results (int): Maximum number of search results to return
            
        Returns:
            SearchResults: OpenAI search results with cleaned content text
        """
        search_results = self.client.vector_stores.search(
            vector_store_id=self.vs_id,
            query=prompt,
            max_num_results=max_results
        )
        for result in search_results.data:
            content = result.content[0].text
            content = re.sub(r'^#{1,6}\s+', '', content, flags=re.MULTILINE)
            content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)
            result.content[0].text = content

        return search_results

    def summarize_history(self, user_question, assistant_response):
        """
        Create a concise summary of the conversation exchange and update chat history.
        
        Uses GPT-4o-mini to summarize the user question and assistant response,
        then maintains a rolling history of the last 5 conversation summaries.
        
        Args:
            user_question (str): The user's input message
            assistant_response (str): The assistant's complete response
            
        Returns:
            None: Updates self.chat_history and self.summary_history in place
        """
        sys_instruction = """
        You will receive user's question and assitant's response. 
        Your task is to create a precise but short summary of the conversation.
        If the assistant's response adds in a follow-up question include it in the summary.        
        
        The format you will receive the messages in is:
        User: (message) | assistant: (response)
        
        Use this format: 
        "User asked about [topic]. 
        Assistant replied with [brief summary]. 
        <If there is a follow up question> Assistant asked if [follow-up questions]"
        """
        history_prompt = f"User: {user_question} | Assistant: {assistant_response}"

        try:
            summary = self.client.responses.create(
                model='gpt-4o-mini',
                input=history_prompt,
                instructions=sys_instruction,
                max_output_tokens=200,
            ).output_text

            self.chat_history.append(summary)
            self.summary_history.append(summary)

            # Keep only last 5
            if len(self.chat_history) > 5:
                self.chat_history = self.chat_history[-5:]

            if len(self.summary_history) > 5:
                self.summary_history = self.summary_history[-5:]

        except Exception as e:
            print("Error in summarize_history:", e)
            return None

    def update_company_info(self, company, ticker):
        """Update company info and rebuild base system prompt"""
        self.company = company
        self.ticker = ticker
        self.base_system_prompt = build_system_prompt(self.confidence_score, self.company, self.ticker)

    def clear_chat_history(self):
        """Clear chat history when switching companies/contexts"""
        self.chat_history = []
        self.summary_history = []

    def _clean_response(self, text):
        """
        Clean and format response text for Streamlit display.
        
        Converts markdown bold formatting to backticks and escapes dollar signs
        to prevent LaTeX rendering issues in Streamlit.
        
        Args:
            text (str): Raw response text from OpenAI
            
        Returns:
            str: Cleaned text safe for Streamlit display
        """
        text = re.sub(r'\*\*(.*?)\*\*', r'`\1`', text)

        # Escape all dollar signs
        text = text.replace('$', '\\$')
        return text
