"""A local LLM selects existing annotation IDs; model prose is never reported."""
import json
import os
from pathlib import Path
import sys


def select(request,device=None):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from gpu_resources import limit_torch
    torch.set_num_threads(request['threads'])
    if device:
        limit_torch(torch,device)
    target = 'cuda:0' if device else 'cpu'
    directory = request['model_directory']
    options = dict(local_files_only=True,trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(directory,**options)
    model = AutoModelForCausalLM.from_pretrained(directory,use_safetensors=True,
        torch_dtype=torch.float16 if device else torch.float32,**options).to(target).eval()
    content = request['task']+'\nFACTS:\n'+json.dumps(request['facts'],ensure_ascii=True)
    prompt = tokenizer.apply_chat_template([{'role':'user','content':content}],tokenize=False,add_generation_prompt=True)
    inputs = tokenizer(prompt,return_tensors='pt',add_special_tokens=False).to(target)
    if inputs.input_ids.shape[-1] > 7000:
        raise ValueError('Annotation evidence exceeds the bounded model context')
    with torch.inference_mode():
        output = model.generate(**inputs,max_new_tokens=512,do_sample=False,pad_token_id=tokenizer.eos_token_id)
    return dict(text=tokenizer.decode(output[0,inputs.input_ids.shape[-1]:],skip_special_tokens=True),
                device=target,local_only=True,statistics_generated_by_model=False)


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    from gpu_resources import lease, environment
    if request['gpu_usage']:
        weights = sum(p.stat().st_size for p in Path(request['model_directory']).glob('*.safetensors'))
        with lease('haplotype annotation selection',minimum_mb=max(4096,(weights+1024**2-1)//1024**2+4096)) as device:
            os.environ.update(environment(device))
            result = select(request,device)
    else:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        result = select(request)
    Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
