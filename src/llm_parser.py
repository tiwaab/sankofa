import boto3
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class LLMParser:
    def __init__(self):
        self.bedrock = boto3.client(
            'bedrock-runtime', 
            region_name='us-east-1',
            config=boto3.session.Config(
                read_timeout=600,
                connect_timeout=60,
                retries={'max_attempts': 3}
            )
        )
    
    def load_prompt(self, prompt_file: str) -> str:
        """Load prompt from file"""
        prompt_path = Path("src") / prompt_file
        with open(prompt_path, 'r') as f:
            return f.read()
    
    def replace_template_vars(self, prompt: str, **kwargs) -> str:
        """Replace template variables in prompt"""
        for key, value in kwargs.items():
            prompt = prompt.replace(f"{{{{{key}}}}}", str(value))
        return prompt
    
    def call_bedrock(self, prompt: str):
        try:
            response = self.bedrock.invoke_model(
                modelId='anthropic.claude-3-sonnet-20240229-v1:0',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8000
                }),
                contentType='application/json',
                accept='application/json'
            )
                
            result = json.loads(response['body'].read())
            content = result['content'][0]['text']
            
            # Extract JSON from response
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            
            if json_start != -1 and json_end > json_start:
                return json.loads(content[json_start:json_end])
            else:
                logger.error("No JSON found in response")
                return {"pages": [], "toc_extracted": {}}
                
        except Exception as e:
            logger.error(f"Bedrock call failed: {e}")
            return {"pages": [], "toc_extracted": {}}
    
    def parse_first_batch(self, textract_output: str, start_page: int, 
                         end_page: int, book_name: str):
        """Parse first batch for TOC extraction"""
        prompt = self.load_prompt("prompts/toc_prompt.txt")
        
        formatted_prompt = self.replace_template_vars(
            prompt,
            TEXTRACT_OUTPUT=textract_output,
            START_PAGE=start_page,
            END_PAGE=end_page,
            BOOK_NAME=book_name
        )
        
        # Add JSON format instruction
        formatted_prompt += "\n\nReturn response as valid JSON with this structure:\n"
        formatted_prompt += '{"toc_extracted": {"page": "chapter"}, "pages": [{"page_number": "1", "content": "...", "chapter": "...", "filename": "....qmd", "chapter_start": true, "has_images": false}]}'
        
        logger.info(f"Sending first batch prompt to Bedrock")
        return self.call_bedrock(formatted_prompt)
    
    def parse_subsequent_batch(self, textract_output: str, start_page: int,
                             end_page: int, book_name: str,
                             toc_mapping: dict = None,
                             current_chapter: str = None):
        """Parse subsequent batch with context"""
        prompt = self.load_prompt("prompts/subsequent_batch_prompt.txt")
        
        formatted_prompt = self.replace_template_vars(
            prompt,
            TEXTRACT_OUTPUT=textract_output,
            START_PAGE=start_page,
            END_PAGE=end_page,
            BOOK_NAME=book_name,
            TOC_MAPPING=json.dumps(toc_mapping or {}),
            CURRENT_CHAPTER=current_chapter or ""
        )
        
        # Add JSON format instruction
        formatted_prompt += "\n\nReturn response as valid JSON with this structure:\n"
        formatted_prompt += '{"pages": [{"page_number": "1", "content": "...", "chapter": "...", "filename": "....qmd", "chapter_start": false, "has_images": false}]}'
        
        logger.info(f"Sending subsequent batch prompt to Bedrock")
        return self.call_bedrock(formatted_prompt)