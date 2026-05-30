"""Sync Ollama models from ollama.com/search to local database."""

import logging
import re
from typing import Optional, Dict, List
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from storage.models import OllamaModelRegistry
from database import db

logger = logging.getLogger(__name__)

# Hardcoded model size mappings for accuracy
MODEL_SIZE_OVERRIDES = {
    'mistral:latest': 7.0,
    'mistral:7b': 7.0,
    'mistral:small': 7.0,
    'llama2': 13.0,
    'llama2:latest': 13.0,
    'llama2:7b': 7.0,
    'llama2:13b': 13.0,
    'llama2:70b': 70.0,
    'llama2-uncensored:7b': 7.0,
    'llama2-uncensored:13b': 13.0,
    'llama2-uncensored:70b': 70.0,
    'llama3': 8.0,
    'llama3:8b': 8.0,
    'llama3:70b': 70.0,
    'llama3.1:8b': 8.0,
    'llama3.1:70b': 70.0,
    'phi': 2.6,
    'phi:2.7b': 2.6,
    'phi3': 3.8,
    'phi3:3.8b': 3.8,
    'neural-chat': 13.0,
    'neural-chat:7b': 7.0,
    'starling-lm:7b': 7.0,
    'openchat': 7.0,
    'openchat:7b': 7.0,
    'dolphin-mixtral': 45.0,
    'mixtral': 45.0,
    'mixtral:8x7b': 45.0,
    'dphil': 7.0,
    'moondream': 1.6,
}

# Model use-case classification
USE_CASE_KEYWORDS = {
    'reasoning': ['reason', 'qwen-qvq', 'qvq'],
    'vision': ['vision', 'llava', 'clip', 'qwen-vl', 'bakllava', 'minicpm-v', 'llavav1.6'],
    'embedding': ['embed', 'bge', 'nomic-embed'],
}


def infer_model_size(name: str, description: str) -> float:
    """
    Infer model size in GB from name and description.
    
    Uses hardcoded overrides first, then heuristics.
    """
    name_lower = name.lower()
    
    # Check overrides
    if name_lower in MODEL_SIZE_OVERRIDES:
        return MODEL_SIZE_OVERRIDES[name_lower]
    
    # Extract from description patterns like "13B", "70B", "7B"
    desc_lower = description.lower() if description else ''
    
    # Try to find size pattern: "70b", "13b", etc.
    match = re.search(r'(\d+(?:\.\d+)?)\s*[bg](?:[^a-z]|$)', name_lower + ' ' + desc_lower)
    if match:
        size = float(match.group(1))
        # Small models (< 1GB) are likely wrong or very special
        if size >= 0.5:
            return size
    
    # Fallback heuristics by model family
    if 'llama3.1:405b' in name_lower or '405b' in name_lower:
        return 405.0
    elif 'llama3.1:70b' in name_lower or 'llama2:70b' in name_lower or '70b' in name_lower:
        return 70.0
    elif 'llama3:70b' in name_lower:
        return 70.0
    elif 'mixtral' in name_lower or 'dbrx' in name_lower:
        return 45.0
    elif '40b' in name_lower:
        return 40.0
    elif 'llama3.1:13b' in name_lower or 'llama2:13b' in name_lower or '13b' in name_lower:
        return 13.0
    elif 'llama3:8b' in name_lower or 'llama2:7b' in name_lower or '7b' in name_lower:
        return 7.0
    elif 'phi3:' in name_lower:
        return 3.8
    elif 'phi' in name_lower:
        return 2.6
    elif 'moondream' in name_lower:
        return 1.6
    
    # Default: assume 7B
    logger.warning(f"Could not infer size for {name}; assuming 7GB")
    return 7.0


def infer_use_case(name: str, description: str) -> str:
    """
    Infer use-case from model name and description.
    
    Returns: "chat", "reasoning", "vision", "embedding"
    """
    combined = (name + ' ' + (description or '')).lower()
    
    for use_case, keywords in USE_CASE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in combined:
                return use_case
    
    # Default
    return 'chat'


def infer_is_multimodal(name: str, description: str) -> bool:
    """Check if model is multimodal (handles images, video, etc.)."""
    combined = (name + ' ' + (description or '')).lower()
    multimodal_keywords = ['vision', 'llava', 'qwen-vl', 'clip', 'bakllava', 'minicpm-v']
    return any(kw in combined for kw in multimodal_keywords)


def compute_hardware_requirements(size_gb: float, is_multimodal: bool, use_case: str) -> tuple:
    """
    Compute min_vram_mb and min_ram_mb from model characteristics.
    
    Returns: (min_vram_mb, min_ram_mb)
    """
    # VRAM requirement ≈ model size + overhead
    vram_mb = int(size_gb * 1024 * 1.1)  # +10% for overhead
    
    # Vision models need extra VRAM for image processing
    if is_multimodal:
        vram_mb += 3072  # +3GB for vision
    
    # System RAM: at least 2x VRAM, min 16GB
    ram_mb = max(int(vram_mb * 2), 16384)
    
    return vram_mb, ram_mb


def fetch_ollama_models() -> List[Dict]:
    """
    Fetch models from ollama.com/search.
    
    Returns list of dicts with: name, description, pull_url
    """
    models = []
    page = 1
    max_pages = 5  # Limit to avoid excessive requests
    
    while page <= max_pages:
        try:
            url = f"https://ollama.com/search?o=newest&p={page}"
            logger.debug(f"Fetching {url}")
            
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find model cards (structure may vary, this is a best-effort parse)
            model_links = soup.find_all('a', href=re.compile(r'^/library/'))
            
            if not model_links:
                logger.debug(f"No models found on page {page}")
                break
            
            page_models = 0
            for link in model_links:
                try:
                    name = link.get_text(strip=True)
                    if not name or ':' not in name:
                        # Skip entries that don't look like model names
                        continue
                    
                    href = link.get('href', '')
                    pull_url = f"https://ollama.com{href}" if href else None
                    
                    # Try to find description from nearby elements
                    desc = ''
                    parent = link.find_parent()
                    if parent:
                        desc_elem = parent.find('p')
                        if desc_elem:
                            desc = desc_elem.get_text(strip=True)
                    
                    models.append({
                        'name': name,
                        'description': desc,
                        'pull_url': pull_url or f"https://ollama.com/library/{name}",
                    })
                    page_models += 1
                except Exception as e:
                    logger.warning(f"Error parsing model link: {e}")
            
            if page_models == 0:
                # No models on this page, stop pagination
                break
            
            page += 1
        except requests.RequestException as e:
            logger.warning(f"Error fetching page {page}: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error on page {page}: {e}")
            break
    
    logger.info(f"Fetched {len(models)} models from ollama.com")
    return models


def sync_ollama_models(app) -> Dict:
    """
    Sync Ollama models from ollama.com/search to database.
    
    Returns: {
        "total_synced": N,
        "new": M,
        "updated": K,
        "errors": E
    }
    """
    with app.app_context():
        try:
            # Fetch from ollama.com
            fetched_models = fetch_ollama_models()
            
            stats = {
                'total_synced': len(fetched_models),
                'new': 0,
                'updated': 0,
                'errors': 0,
            }
            
            for model_data in fetched_models:
                try:
                    name = model_data['name']
                    description = model_data['description']
                    pull_url = model_data['pull_url']
                    
                    # Infer metadata
                    size_gb = infer_model_size(name, description)
                    use_case = infer_use_case(name, description)
                    is_multimodal = infer_is_multimodal(name, description)
                    min_vram_mb, min_ram_mb = compute_hardware_requirements(
                        size_gb, is_multimodal, use_case
                    )
                    
                    # Check if model already exists
                    existing = OllamaModelRegistry.query.filter_by(model_name=name).first()
                    
                    if existing:
                        # Update metadata
                        existing.size_gb = size_gb
                        existing.use_case = use_case
                        existing.is_multimodal = is_multimodal
                        existing.description = description
                        existing.pull_url = pull_url
                        existing.min_vram_mb = min_vram_mb
                        existing.min_ram_mb = min_ram_mb
                        existing.last_sync = datetime.utcnow()
                        stats['updated'] += 1
                    else:
                        # Insert new model
                        model = OllamaModelRegistry(
                            model_name=name,
                            size_gb=size_gb,
                            use_case=use_case,
                            is_multimodal=is_multimodal,
                            description=description,
                            pull_url=pull_url,
                            is_loaded=False,  # Default: not loaded until user pulls it
                            min_vram_mb=min_vram_mb,
                            min_ram_mb=min_ram_mb,
                        )
                        db.session.add(model)
                        stats['new'] += 1
                
                except Exception as e:
                    logger.error(f"Error syncing model {model_data.get('name')}: {e}")
                    stats['errors'] += 1
            
            # Commit all changes
            db.session.commit()
            
            logger.info(
                f"Model sync complete: {stats['total_synced']} total, "
                f"{stats['new']} new, {stats['updated']} updated, {stats['errors']} errors"
            )
            
            return stats
        
        except Exception as e:
            logger.error(f"Fatal error in sync_ollama_models: {e}")
            db.session.rollback()
            raise
