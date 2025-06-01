from pydantic_ai import Agent
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from pathlib import Path
import json

class TitlePage(BaseModel):
    title: str = ""
    author: str = ""
    year: str = ""
    publisher: str = ""

class PageMetadata(BaseModel):
    page_number: str
    content: str
    chapter: str
    filename: str
    chapter_start: bool = False
    has_images: bool = False

class Image(BaseModel):
    filename: str
    path: str
    caption: str

class BatchResponse(BaseModel):
    toc_extracted: Dict[str, str] = Field(default_factory=dict)
    title_page: Optional[TitlePage] = None
    pages: List[PageMetadata] = Field(default_factory=list)  # Empty list instead of required
    images: List[Image] = Field(default_factory=list)

class LLMParser:
    def __init__(self):
        self.agent = Agent(
        'bedrock:anthropic.claude-3-sonnet-20240229-v1:0',
        result_type=BatchResponse
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
    
    def parse_first_batch(self, textract_output: str, start_page: int, 
                         end_page: int, book_name: str) -> BatchResponse:
        """Parse first batch for TOC extraction"""
        prompt = self.load_prompt("prompts/toc_prompt.txt")
        
        formatted_prompt = self.replace_template_vars(
            prompt,
            TEXTRACT_OUTPUT=textract_output,
            START_PAGE=start_page,
            END_PAGE=end_page,
            BOOK_NAME=book_name
        )
        
        result = self.agent.run_sync(formatted_prompt)
        return result.data
    
    def parse_subsequent_batch(self, textract_output: str, start_page: int,
                             end_page: int, book_name: str,
                             toc_mapping: Dict[str, str] = None,
                             current_chapter: str = None) -> BatchResponse:
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
        
        result = self.agent.run_sync(formatted_prompt)
        return result.data