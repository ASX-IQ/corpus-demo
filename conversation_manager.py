from collections import Counter
import duckdb
import boto3
import json
import botocore.exceptions
import csv
import os
from datetime import datetime

class ConversationManager:
    def __init__(self, aws_id, aws_key, aws_region):
        """
        Initialize ConversationManager with database and AWS connections.
        
        Args:
            aws_id (str): AWS access key ID for Lambda and S3 operations
            aws_key (str): AWS secret access key for Lambda and S3 operations  
            aws_region (str): AWS region for service connections
        """
        self.conn = duckdb.connect()
        self.aws_id = aws_id
        self.aws_key = aws_key
        self.aws_region = aws_region
        self.ticker = None
        self.user_email = None
        self.date_from = None
        self.date_to = None
        self.announcement_types = None
        self.price_sensitive = False

    def get_companies_data(self):
        """
        Fetch all unique companies and their ticker symbols from the database.

        Returns:
            dict: Dictionary mapping company names to ticker symbols
                  Format: {'Company Name': 'TICKER', ...}
        """

        query = """SELECT DISTINCT "Company Name", "Ticker" FROM read_csv('data/companies.csv', header=True)"""
        result = self.conn.execute(query).fetchall()

        companies_dict = {row[0]: row[1] for row in result}

        return companies_dict

    def _build_query(self):
        """
        Build dynamic SQL query for fetching ASX announcements based on instance filters.
        
        Constructs a query that filters by ticker, date range, price sensitivity,
        and announcement types using regex patterns. Prioritizes markdown files.
        
        Returns:
            str: Complete SQL query string with all applied filters
        """
        query = f"""
            SELECT 
                Ticker, 
                url, 
                Key,
                announcementTypes
            FROM read_csv('data/announcements.csv', header=True)
            WHERE 1=1
            AND Ticker = '{self.ticker}'
            AND date >= '{self.date_from}' 
            AND date <= '{self.date_to}'
            """

        if self.price_sensitive:
            query += f"AND isPriceSensitive = True\n"


        if self.announcement_types:
            # Map announcement types to their regex patterns
            type_patterns = {
                'Cashflow Reports': "regexp_matches(\"announcementTypes\", 'Cash', 'i')",
                'Mining studies/resources': "regexp_matches(\"announcementTypes\", 'dfs|pfs|scoping|study|feasibility|jorc|resource', 'i')",
                'Placements': "regexp_matches(\"announcementTypes\", 'Placement|Renounceable|Security Purchase|Trading Halt', 'i')",
                "Shares 3B's, 2A's": "regexp_matches(\"announcementTypes\", 'Placement|Appendix 2A|Appendix 3B|Renounceable|Security Purchase|Appendix 3G|Trading Halt', 'i')",
                'Presentations': "regexp_matches(\"announcementTypes\", 'presentation', 'i')"
            }

            # Build OR conditions for selected types
            conditions = [type_patterns[t] for t in self.announcement_types if t in type_patterns]

            if conditions:
                and_statement = f"AND ({' OR '.join(conditions)})"
                query += and_statement

        return query

    def get_s3_keys(self):
        """
        Execute the built query and extract S3 keys and announcement type statistics.
        
        Uses the query built by _build_query() to fetch document keys and count
        announcement types for the current filter criteria.
        
        Returns:
            tuple: (keys_list, types_counted) where keys_list contains S3 object keys
                   and types_counted is a Counter object with announcement type frequencies
        """
        query = self._build_query()
        result = self.conn.execute(query).fetchall()

        # Get the list of urls to download
        keys_list = [url[2] for url in result]

        # Get the list of types and count them
        types_list = [url[3] for url in result]
        types_counted = Counter(types_list)

        return keys_list, types_counted

    def create_payload(self, keys, vs_id):
        """
        Create AWS Lambda payload for S3 file processing.
        
        Formats the S3 keys and vector store ID into the expected Lambda event structure
        for the pdf_upload_vs function.
        
        Args:
            keys (list): List of S3 object keys to process
            vs_id (str): OpenAI vector store ID for document upload
            
        Returns:
            dict: Lambda event payload in AWS S3 event format
        """
        return {
            "Records": [
                {
                    "s3": {
                        "bucket": {
                            "name": "asx-storage"
                        },
                        "object": {
                            "keys": keys,
                            "vs_id": vs_id
                        }
                    }
                }
            ]
        }

    def lambda_s3_files_upload(self, function_name='pdf_upload_vs', payload=None):
        """
        Invoke AWS Lambda function to upload S3 files to OpenAI vector store.
        
        Creates Lambda client and invokes the specified function with the payload.
        Handles timeout errors gracefully.
        
        Args:
            function_name (str, optional): Lambda function name. Defaults to 'pdf_upload_vs'
            payload (dict, optional): Lambda payload. Defaults to empty dict
            
        Returns:
            dict or None: Lambda response data, None if timeout occurs
        """
        lambda_client = boto3.client(
            'lambda',
            aws_access_key_id=self.aws_id,
            aws_secret_access_key=self.aws_key,
            region_name=self.aws_region
        )

        try:
            response = lambda_client.invoke(
                FunctionName=function_name,
                Payload=json.dumps(payload or {})
            )
            result = json.loads(response['Payload'].read())
            return result
        except botocore.exceptions.ReadTimeoutError:
            return None

    def save_user_data_to_db(self, user_data):
        """
        Save conversation session data to a local CSV file.
        
        Appends a complete conversation record including user info, vector store data,
        query parameters, messages, and chat settings to data/conversations.csv.
        
        Args:
            user_data (dict): Structured conversation data containing:
                             - session_id, user info, vector_store details
                             - query parameters, message content, chat_settings
                             
        Raises:
            Exception: Prints error message if CSV write fails
        """
        # Extract data from user_data dict
        session_id = user_data['session_id']
        # User
        user_id = user_data['user']['user_id']
        user_email = user_data['user']['user_email']

        # Vector Store
        vector_store_id = user_data['vector_store']['vs_id']
        num_of_docs = user_data['vector_store']['num_of_docs']
        s3_keys = user_data['vector_store']['s3_keys']

        # Selections
        ticker = user_data['query']['selected_ticker']
        announcement_types = user_data['query']['announcement_types']
        price_sensitive = user_data['query']['price_sensitive']
        date_from = user_data['query']['date_from']
        date_to = user_data['query']['date_to']
        date_range = user_data['query']['date_range']

        # Message
        message_text = user_data['message']['message_text']
        message_timestamp = user_data['message']['message_timestamp']
        assistant_response = user_data['message']['assistant_response']

        # Chat settings
        chat_model = user_data['chat_settings']['chat_model']
        chat_mode = user_data['chat_settings']['chat_mode']
        chat_tokens = user_data['chat_settings']['tokens_used']

        # Prepare CSV file path
        csv_file_path = 'data/conversations.csv'
        file_exists = os.path.exists(csv_file_path)
        
        # Ensure data directory exists
        os.makedirs('data', exist_ok=True)
        
        try:
            with open(csv_file_path, 'a', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                
                # Write header if file doesn't exist
                if not file_exists:
                    writer.writerow([
                        'session_id', 'user_id', 'user_email', 'vector_store_id', 
                        'num_of_docs', 's3_keys', 'ticker', 'announcement_types', 
                        'price_sensitive', 'date_from', 'date_to', 'date_range',
                        'message_text', 'assistant_response', 'message_timestamp', 
                        'chat_model', 'chat_mode', 'tokens_used'
                    ])
                
                # Write data row
                writer.writerow([
                    session_id, user_id, user_email, vector_store_id,
                    num_of_docs, s3_keys, ticker, announcement_types,
                    price_sensitive, date_from, date_to, date_range,
                    message_text, assistant_response, message_timestamp,
                    chat_model, chat_mode, chat_tokens
                ])
        except Exception as e:
            print(f'Failed to save to CSV file. {e}')
