import os
import torch
import argparse
import json
from torch.utils.data import DataLoader
from tqdm import tqdm
import logging
from models.graphllm_pla_v2 import GraphLLM
from framework.train import PlannerDataset
from safetensors.torch import load_model
from framework.train import improved_collate_fn

def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def load_trained_model(args, checkpoint_path):
    """Load trained model from checkpoint"""
    model = GraphLLM(args)
    
    try:
        load_model(model, checkpoint_path)
        logging.info(f"Successfully loaded checkpoint from {checkpoint_path}")
        return model
    except Exception as e:
        logging.error(f"Error loading checkpoint: {str(e)}")
        raise

def run_inference(model, test_loader):
    """Run inference on test data"""
    model.eval()
    all_results = []
    
    with torch.no_grad():
        for batch in tqdm(test_loader, desc="Running inference"):
            try:
                outputs = model.inference(batch)
                
                # Store results for each sample in batch
                for i in range(len(outputs['input'])):
                    result = {
                        'input': outputs['input'][i],
                        'prediction': outputs['pred'][i].split("</s>")[0],
                        'label': outputs['label'][i]
                    }
                    all_results.append(result)
                    print(f"INPUT: {outputs['input'][i]}")
                    print(f"PREDICTION: {outputs['pred'][i].split('</s>')[0]}")
                    print(f"LABEL: {outputs['label'][i]}")
                    
            except Exception as e:
                logging.error(f"Error during inference: {str(e)}")
                continue
                
    return all_results

def save_results(results, output_path):
    """Save results to JSON file"""
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logging.info(f"Results saved to {output_path}")
    except Exception as e:
        logging.error(f"Error saving results: {str(e)}")

def main():
    parser = argparse.ArgumentParser()
    # Model arguments (must match training arguments)
    parser.add_argument('--llm_model_path', type=str, default='meta-llama/Llama-2-7b-hf')
    parser.add_argument('--llm_frozen', type=str, default='False')
    parser.add_argument('--finetune_method', type=str, default='lora')
    parser.add_argument('--gnn_model_name', type=str, default='gt')
    parser.add_argument('--gnn_in_dim', type=int, default=1024)
    parser.add_argument('--gnn_hidden_dim', type=int, default=1024)
    parser.add_argument('--gnn_num_layers', type=int, default=3)
    parser.add_argument('--gnn_dropout', type=float, default=0.1)
    parser.add_argument('--gnn_num_heads', type=int, default=8)
    parser.add_argument('--max_txt_len', type=int, default=2500)
    parser.add_argument('--max_new_tokens', type=int, default=150)  
    
    # Test specific arguments
    parser.add_argument('--lora_r', type=int, default=8)
    parser.add_argument('--lora_alpha', type=int, default=16)
    parser.add_argument('--lora_dropout', type=float, default=0.05)
    
    parser.add_argument('--checkpoint_path', type=str, required=True,
                        help='Path to model checkpoint')
    parser.add_argument('--test_data_path', type=str, required=True,
                        help='Path to test data pickle file')
    parser.add_argument('--output_path', type=str, required=True,
                        help='Path to save results JSON')
    parser.add_argument('--batch_size', type=int, default=4)
    parser.add_argument('--num_test_samples', type=int, default=100,
                        help='Number of test samples to process')
    
    args = parser.parse_args()
    
    # Setup
    logger = setup_logging()
    
    # Load model
    logger.info("Loading model...")
    model = load_trained_model(args, args.checkpoint_path)
    
    # Load test dataset
    logger.info("Loading test dataset...")
    test_dataset = PlannerDataset(args.test_data_path)
    test_dataset_small = torch.utils.data.Subset(test_dataset, range(args.num_test_samples))
    
    # Create test dataloader
    test_loader_small = DataLoader(
        test_dataset_small,
        batch_size=args.batch_size,
        shuffle=False,
        drop_last=True,
        collate_fn=improved_collate_fn
    )
    
    # Run inference
    logger.info("Running inference...")
    results = run_inference(model, test_loader_small)
    
    # Save results
    logger.info("Saving results...")
    save_results(results, args.output_path)
    
    logger.info("Testing completed!")

if __name__ == '__main__':
    main()