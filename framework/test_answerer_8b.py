import os
import torch
import argparse
import json
from torch.utils.data import DataLoader
from tqdm import tqdm
import logging
from models.graphllm_8b import GraphLLM
# from models.graphllm_8b_npgraph import GraphLLM
from train_answerer import improved_collate_fn
from safetensors.torch import load_model
import pickle
from torch.utils.data import Dataset


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
    
def load_base_model(args):
    """Load base model without trained weights"""
    model = GraphLLM(args)
    logging.info("Testing with base LLaMA-2-7B model and untrained GNN")
    return model

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
                        'prediction': outputs['pred'][i].split("<|end_of_text|>")[0],
                        'label': outputs['label'][i]
                    }
                    all_results.append(result)
                    print(f"INPUT: {outputs['input'][i]}")
                    print(f"PREDICTION: {outputs['pred'][i].split('<|end_of_text|>')[0]}")
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


class AnswererDataset(Dataset):
    def __init__(self, data_path):
        self.data = pickle.load(open(data_path, 'rb'))
        self.data_path = data_path
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        if "asqa" in self.data_path or "eli5" in self.data_path:
            return {
                'input': self.data[idx]['input'].replace("\nQuestion: ", "\n[Long Form] Question: "),
                'label': self.data[idx]['label'],
                'graphs': self.data[idx]['graphs']
            }
        elif "arc" in self.data_path:
            return {
                'input': self.data[idx]['input'].replace("\n\nQuestion: Which is true? \n\n### Input:\n", "\n\nQuestion: Given four answer candidates, A, B, C and D, choose the best answer choice.## Input:\n\n"),
                'label': self.data[idx]['label'],
                'graphs': self.data[idx]['graphs']
            }
        else:
            return {
                'input': self.data[idx]['input'].replace("</s>", ""),
                'label': self.data[idx]['label'],
                'graphs': self.data[idx]['graphs']
            }
        
        
def main():
    parser = argparse.ArgumentParser()
    # Model arguments (must match training arguments)
    parser.add_argument('--llm_model_path', type=str, default='meta-llama/Meta-Llama-3-8B')
    parser.add_argument('--llm_frozen', type=str, default='False')
    parser.add_argument('--finetune_method', type=str, default='lora')
    parser.add_argument('--gnn_model_name', type=str, default='gt')
    parser.add_argument('--gnn_in_dim', type=int, default=1024)
    parser.add_argument('--gnn_hidden_dim', type=int, default=1024)
    parser.add_argument('--gnn_num_layers', type=int, default=3)
    parser.add_argument('--gnn_dropout', type=float, default=0.1)
    parser.add_argument('--gnn_num_heads', type=int, default=8)
    parser.add_argument('--max_txt_len', type=int, default=2500)
    parser.add_argument('--max_new_tokens', type=int, default=300)
    
    
    parser.add_argument('--lora_r', type=int, default=8)
    parser.add_argument('--lora_alpha', type=int, default=16)
    parser.add_argument('--lora_dropout', type=float, default=0.05)
    # Test specific arguments
    parser.add_argument('--checkpoint_path', type=str, required=True,
                        help='Path to model checkpoint')
    parser.add_argument('--test_data_path', type=str, required=True,
                        help='Path to test data pickle file')
    parser.add_argument('--output_path', type=str, required=True,
                        help='Path to save results JSON')
    parser.add_argument('--batch_size', type=int, default=4)
    
    args = parser.parse_args()
    
    # Setup
    logger = setup_logging()
    
    # Load model
    logger.info("Loading model...")
    model = load_trained_model(args, args.checkpoint_path)
    # model = load_base_model(args)
    
    # Load test dataset
    logger.info("Loading test dataset...")
    test_dataset_small = AnswererDataset(args.test_data_path)
    # test_dataset_small = torch.utils.data.Subset(test_dataset_small, range(100))
    
    # Create test dataloader
    test_loader = DataLoader(
        test_dataset_small,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=improved_collate_fn
    )
    
    # Run inference
    logger.info("Running inference...")
    results = run_inference(model, test_loader)
    
    # Save results
    logger.info("Saving results...")
    save_results(results, args.output_path)
    
    logger.info("Testing completed!")

if __name__ == '__main__':
    main()