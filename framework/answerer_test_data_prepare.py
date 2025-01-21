from utils import *
import json
import pickle
from tqdm import tqdm

ANSWERER_INSTRUCTION = get_answerer_instruction("ras")


def convert_triple_str_to_list(triple_str):
        
    triples = [triple + ')' if not triple.endswith(')') else triple 
                                for triple in triple_str.split('), ')] 
    return triples

def convert_triple_str_to_graph(triple_str, graph_processor):
    triples = convert_triple_str_to_list(triple_str)
    try:
        graph, triples = graph_processor.create_graph_from_triples(triples)
    except Exception as e:
        print(f"Error processing triples: {triples}")
        print(f"Error: {e}")
        return None, None
    return graph, triples


def load_data(path):
    with open(path, 'r') as f:
        data = json.load(f)
    return data


def construct_data_samples(dataset, data, graph_processor):
    questions = data['question']
    answers = data['answer']
    triple_strs = data['triple_lists']
    subqueries = data['subqueries']
    
    data_samples = []
    
    for i in tqdm(range(len(questions))):
        if dataset == "pubhealth":
            # question = "Is the following statement correct or not? Say 'true' if it's correct; otherwise say 'false' in lowercase. Do not say anything else, and only output 'true' or 'false'." + "\nStatement: " + questions[i]
            question = "Is statement 'true' or 'false'? Only output 'true' or 'false'." + "\nStatement: " + questions[i]
            pure_question = questions[i]
        elif dataset == "arc_c":
            question = questions[i].replace("Given four answer candidates, A, B, C and D, choose the best answer choice. Only output the letter of the answer choice, e.g. A, B, C, or D. Do not say anything else, and only output A, B, C, or D.\n\n### Input:\n", "Which is true? Output A, B, C, or D.\n\n### Input:\n")
            pure_question = questions[i].replace("Given four answer candidates, A, B, C and D, choose the best answer choice. Only output the letter of the answer choice, e.g. A, B, C, or D. Do not say anything else, and only output A, B, C, or D.\n\n### Input:\n", "")
        elif dataset == "2wikimultihop":
            question = questions[i].replace("\n### Input:\n", "")
        else:
            question = questions[i]
        answer = answers[i]
        triple_strs_ = triple_strs[i]
        subqueries_ = subqueries[i]
        
        graphs_pre = []
        triples_pre = []
        graphs = []
        triples = []
        
        input_ = ""
        if len(subqueries_) > 0:
            removed_subqueries_idx = []
            for j in range(len(subqueries_)):
                subquery = subqueries_[j]
                triple_str = triple_strs_[j]
                graph, triples_ = convert_triple_str_to_graph(triple_str, graph_processor)
                
                if graph is None or triples_ is None:
                    removed_subqueries_idx.append(j)
                    graphs_pre.append("")
                    triples_pre.append("")
                    continue
                    
                graphs_pre.append(graph)
                triples_pre.append(triples_)
            
            # print("triples: ", triples_pre)
            # print("length of triples: ", len(triples_pre))
            # print("length of subqueries_: ", len(subqueries_))
            # print("removed_subqueries_idx: ", removed_subqueries_idx)
            
            if len(graphs_pre) > 0:
                for j in range(len(subqueries_)):
                    if j in removed_subqueries_idx:
                        continue
                    if dataset == "arc_c":
                        input_ += "[SUBQ] " + pure_question.split("\nA:")[0] + "\n" + "Retrieved Graph Information: " + str(triples_pre[j]) + "\n"
                    elif dataset == "pubhealth":
                        input_ += "[SUBQ] " + pure_question + "\n" + "Retrieved Graph Information: " + str(triples_pre[j]) + "\n"
                    else:
                        input_ += "[SUBQ] " + subqueries_[j].replace("[SUBQ] ", "") + "\n" + "Retrieved Graph Information: " + str(triples_pre[j]) + "\n"
                    graphs.append(graphs_pre[j])
                    triples.append(triples_pre[j])

                input_ += "Question: " + question
                
                print(input_)
                processed_item = {
                    'input': ANSWERER_INSTRUCTION + "\n" + input_,
                    'label': answer,
                    'graphs': graphs,
                    'triples': triples,
                }
                
                
            else:
                input_ = ANSWERER_INSTRUCTION + "\nQuestion: " + question
                processed_item = {
                    'input': input_,
                    'label': answer,
                    'graphs': graphs,
                }
                
            data_samples.append(processed_item)

        else:
            input_ = ANSWERER_INSTRUCTION + "\nQuestion: " + question
            processed_item = {
                'input': input_,
                'label': answer,
                'graphs': graphs,
            }
            data_samples.append(processed_item)
            
    return data_samples


def main():
    graph_processor = GraphProcessor()
    dataset = "eli5"
    
    input_data_path = f"/shared/eng/pj20/firas_data/test_datasets/{dataset}_test_output_graphllm_graphllm.json"
    input_data = load_data(input_data_path)
    # input_data = {
    #     "question": input_data['question'][:350],
    #     "answer": input_data['answer'][:350],
    #     "triple_lists": input_data['triple_lists'][:350],
    #     "subqueries": input_data['subqueries'][:350],
    # }
    data_samples = construct_data_samples(dataset, input_data, graph_processor)
    
    with open(f"/shared/eng/pj20/firas_data/test_datasets/answerer/{dataset}_test_output_graphllm_graphllm_answerer_data.pkl", "wb") as f:
        pickle.dump(data_samples, f)
    
    
if __name__ == "__main__":
    main()



