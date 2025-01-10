import os
import torch
from torch.utils.data import Dataset, DataLoader
import logging
import argparse
from models.action_planner import ActionPlanner
from torch.optim import AdamW
import wandb
from tqdm import tqdm
import datetime
import math
from torch.optim.lr_scheduler import LambdaLR

class PlannerDataset(Dataset):
    def __init__(self, data_path):
        self.data = torch.load(data_path)
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        return {
            'input': self.data[idx]['input'],
            'label': self.data[idx]['label'],
            'graphs': self.data[idx]['graphs']
        }

def setup_logging(args):
    os.makedirs('planner/logs', exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join('planner/logs', f'training_{timestamp}.log')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def setup_wandb(args):
    """Initialize wandb"""
    os.environ['WANDB_START_METHOD'] = 'thread'
    run_id = datetime.datetime.now().strftime('run_%Y%m%d_%H%M%S')
    os.environ['WANDB_RUN_ID'] = run_id

    wandb.init(
        project="action-planner-hotpotqa",
        config=args,
        settings=wandb.Settings(start_method="thread")
    )
    return True

def get_cosine_schedule_with_warmup(optimizer, num_warmup_steps, num_training_steps, num_cycles=0.5):
    def lr_lambda(current_step):
        if current_step < num_warmup_steps:
            return float(current_step) / float(max(1, num_warmup_steps))
        progress = float(current_step - num_warmup_steps) / float(max(1, num_training_steps - num_warmup_steps))
        return max(0.0, 0.5 * (1.0 + math.cos(math.pi * float(num_cycles) * 2.0 * progress)))

    return LambdaLR(optimizer, lr_lambda)

def train_epoch(model, train_loader, optimizer, scheduler, epoch, args):
    model.train()
    total_loss = 0
    optimizer.zero_grad()
    
    progress_bar = tqdm(train_loader, desc=f'Training Epoch {epoch}')
    running_loss = []
    
    # Add checkpoint tracking
    total_steps = len(train_loader)
    checkpoint_interval = total_steps // 4  # Save 4 times per epoch
    
    for batch_idx, batch in enumerate(progress_bar):
        try:
            # Forward pass
            loss = model(batch)
            loss = loss / args.grad_accum_steps
            
            # Backward pass
            loss.backward()
            
            # Track gradient norms
            grad_norm = 0.0
            for param in model.parameters():
                if param.grad is not None:
                    grad_norm += param.grad.data.norm(2).item() ** 2
            grad_norm = grad_norm ** 0.5
            
            # Gradient accumulation and optimization step
            if (batch_idx + 1) % args.grad_accum_steps == 0:
                if args.grad_clip > 0:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
                
                optimizer.step()
                optimizer.zero_grad()
                scheduler.step()
            
            # Loss tracking and logging
            total_loss += loss.item() * args.grad_accum_steps
            running_loss.append(loss.item() * args.grad_accum_steps)
            if len(running_loss) > 100:
                running_loss.pop(0)
            smoothed_loss = sum(running_loss) / len(running_loss)
            
            # Update progress bar
            progress_bar.set_postfix({
                'loss': smoothed_loss,
                'lr': scheduler.get_last_lr()[0],
                'grad_norm': grad_norm
            })
            
            # Log to wandb
            if batch_idx % args.log_interval == 0:
                wandb.log({
                    'train_loss': loss.item() * args.grad_accum_steps,
                    'smoothed_loss': smoothed_loss,
                    'learning_rate': scheduler.get_last_lr()[0],
                    'gradient_norm': grad_norm,
                    'epoch': epoch,
                    'step': batch_idx + epoch * len(train_loader)
                })
                
            # Add checkpoint saving logic
            if checkpoint_interval > 0 and (batch_idx + 1) % checkpoint_interval == 0:
                checkpoint_step = (batch_idx + 1) // checkpoint_interval
                model_path = os.path.join(args.output_dir, f'checkpoint_epoch_{epoch}_step_{checkpoint_step}.pt')
                torch.save({
                    'epoch': epoch,
                    'step': batch_idx,
                    'model_state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),
                    'scheduler_state_dict': scheduler.state_dict(),
                    'train_loss': total_loss / (batch_idx + 1),
                }, model_path)
                logging.info(f'Saved intermediate checkpoint to {model_path}')
                
        except RuntimeError as e:
            logging.error(f"Error in batch {batch_idx}: {str(e)}")
            continue
    
    return total_loss / len(train_loader)

def evaluate(model, val_loader):
    model.eval()
    total_loss = 0
    valid_batches = 0
    all_predictions = []
    all_labels = []
    
    with torch.no_grad():
        for batch in tqdm(val_loader, desc='Evaluating'):
            try:
                # Get loss and predictions
                outputs = model.inference(batch)
                loss = model(batch)
                
                # Track metrics
                total_loss += loss.item()
                valid_batches += 1
                all_predictions.extend(outputs['pred'])
                all_labels.extend(batch['label'])
                
            except RuntimeError as e:
                logging.error(f"Error in validation: {str(e)}")
                continue
    
    if valid_batches == 0:
        logging.warning("No valid batches during evaluation!")
        return {
            'val_loss': float('inf'),
            'predictions': [],
            'labels': []
        }
    
    return {
        'val_loss': total_loss / valid_batches,
        'predictions': all_predictions,
        'labels': all_labels
    }

def main():
    parser = argparse.ArgumentParser()
    # Model arguments
    parser.add_argument('--llm_model_path', type=str, default='meta-llama/Llama-2-7b-hf')
    parser.add_argument('--llm_frozen', type=str, default='False')
    parser.add_argument('--finetune_method', type=str, default='lora', choices=['full', 'lora'])
    parser.add_argument('--gnn_model_name', type=str, default='gt')
    parser.add_argument('--gnn_in_dim', type=int, default=1024)
    parser.add_argument('--gnn_hidden_dim', type=int, default=1024)
    parser.add_argument('--gnn_num_layers', type=int, default=3)
    parser.add_argument('--gnn_dropout', type=float, default=0.1)
    parser.add_argument('--gnn_num_heads', type=int, default=8)
    
    # Training arguments
    parser.add_argument('--data_dir', type=str, default='/shared/eng/pj20/firas_data/action_planner/hotpot_train')
    parser.add_argument('--output_dir', type=str, default='/shared/eng/pj20/firas_data/action_planner/hotpot_train/checkpoints')
    parser.add_argument('--max_txt_len', type=int, default=1500)
    parser.add_argument('--max_new_tokens', type=int, default=128)  # Shorter for planning decisions
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--warmup_ratio', type=float, default=0.15)
    parser.add_argument('--grad_accum_steps', type=int, default=4)
    parser.add_argument('--grad_clip', type=float, default=0.5)
    parser.add_argument('--weight_decay', type=float, default=0.01)
    parser.add_argument('--log_interval', type=int, default=10)
    parser.add_argument('--save_interval', type=int, default=5)
    
    # LoRA arguments
    parser.add_argument('--lora_r', type=int, default=8)
    parser.add_argument('--lora_alpha', type=int, default=16)
    parser.add_argument('--lora_dropout', type=float, default=0.05)
    
    # Add debug mode argument
    parser.add_argument('--debug', action='store_true', help='Run in debug mode with validation data only')
    
    args = parser.parse_args()
    
    if args.finetune_method == 'full':
        args.batch_size = max(1, args.batch_size // 4)
    
    # Setup
    logger = setup_logging(args)
    wandb_enabled = setup_wandb(args)
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Load datasets
    logger.info("Loading datasets...")
    if args.debug:
        logger.info("Debug mode: Loading validation data only...")
        val_dataset = PlannerDataset(os.path.join(args.data_dir, 'val.pt'))
        train_dataset = val_dataset  # Use validation data for training in debug mode
        args.epochs = 1  # Reduce epochs for debugging
        args.batch_size = min(4, args.batch_size)  # Smaller batch size for debugging
    else:
        train_dataset = PlannerDataset(os.path.join(args.data_dir, 'train.pt'))
        val_dataset = PlannerDataset(os.path.join(args.data_dir, 'val.pt'))
    
    # Initialize model
    logger.info("Initializing model...")
    model = ActionPlanner(args)
    
    # Setup data loaders
    train_loader = DataLoader(
        train_dataset, 
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=lambda x: {
            'input': [item['input'] for item in x],
            'label': [item['label'] for item in x],
            'graphs': [item['graphs'] for item in x]
        }
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=lambda x: {
            'input': [item['input'] for item in x],
            'label': [item['label'] for item in x],
            'graphs': [item['graphs'] for item in x]
        }
    )
    
    # Training setup
    optimizer = AdamW(model.parameters(), 
                     lr=args.lr,
                     weight_decay=args.weight_decay,
                     betas=(0.9, 0.95))
    
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_steps
    )
    
    # Training loop
    logger.info("Starting training...")
    best_val_loss = float('inf')
    
    for epoch in range(args.epochs):
        train_loss = train_epoch(model, train_loader, optimizer, scheduler, epoch, args)
        val_metrics = evaluate(model, val_loader)
        val_loss = val_metrics['val_loss']
        
        logger.info(f'Epoch {epoch} - Train Loss: {train_loss:.4f}')
        logger.info(f'Epoch {epoch} - Validation Loss: {val_loss:.4f}')
        
        if wandb_enabled:
            wandb.log({
                'epoch': epoch,
                'train_loss_epoch': train_loss,
                'val_loss_epoch': val_loss
            })
        
        # Save best model
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            model_path = os.path.join(args.output_dir, f'best_model.pt')
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'scheduler_state_dict': scheduler.state_dict(),
                'best_val_loss': best_val_loss,
                'val_predictions': val_metrics['predictions'],
                'val_labels': val_metrics['labels']
            }, model_path)
            logger.info(f'Saved best model to {model_path}')
        
        # Save checkpoint
        # if (epoch + 1) % args.save_interval == 0:
        #     model_path = os.path.join(args.output_dir, f'checkpoint_epoch_{epoch}.pt')
        #     torch.save({
        #         'epoch': epoch,
        #         'model_state_dict': model.state_dict(),
        #         'optimizer_state_dict': optimizer.state_dict(),
        #         'scheduler_state_dict': scheduler.state_dict(),
        #         'val_loss': val_loss,
        #     }, model_path)
    
    if wandb_enabled:
        wandb.finish()

if __name__ == '__main__':
    main()