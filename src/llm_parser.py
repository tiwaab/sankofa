import boto3
import json
import logging
import re
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
        prompt_path = Path("src/prompts") / prompt_file.split('/')[-1]
        with open(prompt_path, 'r') as f:
            return f.read()

    
    def replace_template_vars(self, prompt: str, **kwargs) -> str:
        """Replace template variables in prompt"""
        for key, value in kwargs.items():
            prompt = prompt.replace(f"{{{{{key}}}}}", str(value))
        return prompt
    
    def call_bedrock_markdown(self, prompt: str):
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
            return content
                
        except Exception as e:
            logger.error(f"Bedrock call failed: {e}")
            return ""
    
    def parse_markdown_response(self, markdown_content: str, toc_mapping: dict = None, current_chapter: str = None):
        """Parse markdown response into structured data"""
        result = {"pages": [], "toc_extracted": {}}
        
        # Extract TOC mapping if present (first batch)
        if "## TOC_MAPPING" in markdown_content:
            toc_match = re.search(r'## TOC_MAPPING\n(.*?)\n\n', markdown_content, re.DOTALL)
            if toc_match:
                try:
                    result["toc_extracted"] = json.loads(toc_match.group(1))
                except:
                    logger.warning("Failed to parse TOC mapping")
        
        # Extract pages
        page_pattern = r'## PAGE (\d+)\n(.*?)(?=## PAGE \d+|\Z)'
        pages = re.findall(page_pattern, markdown_content, re.DOTALL)
        
        for page_num, content in pages:
            # Determine chapter
            chapter = self._determine_chapter(page_num, content, result.get("toc_extracted", {}), toc_mapping, current_chapter)
            
            # Check if it's a chapter start
            chapter_start = self._is_chapter_start(content)
            
            # Check if it has images
            has_images = "![" in content
            
            result["pages"].append({
                "page_number": page_num,
                "content": content.strip(),
                "chapter": chapter,
                "chapter_start": chapter_start,
                "has_images": has_images
            })
        
        return result
    
    def _determine_chapter(self, page_num, content, new_toc, existing_toc, current_chapter):
        """Determine chapter for a page"""
        # Check new TOC first
        if page_num in new_toc:
            return new_toc[page_num]
        
        # Check existing TOC
        if existing_toc and page_num in existing_toc:
            return existing_toc[page_num]
        
        # Check for chapter heading in content
        chapter_match = re.search(r'^# (.*)', content, re.MULTILINE)
        if chapter_match:
            title = chapter_match.group(1).lower()
            return re.sub(r'[^a-zA-Z0-9\-]', '-', title)
        
        # Default to current chapter or frontmatter
        return current_chapter or "frontmatter"
    
    def _is_chapter_start(self, content):
        """Check if page contains a chapter start"""
        return bool(re.search(r'^# ', content, re.MULTILINE))
    
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
        
        logger.info(f"Sending first batch prompt to Bedrock")
        markdown_response = self.call_bedrock_markdown(formatted_prompt)
        return self.parse_markdown_response(markdown_response)
    
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
        
        logger.info(f"Sending subsequent batch prompt to Bedrock")
        markdown_response = self.call_bedrock_markdown(formatted_prompt)
        return self.parse_markdown_response(markdown_response, toc_mapping, current_chapter)