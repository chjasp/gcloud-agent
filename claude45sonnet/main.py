#!/usr/bin/env python3
"""
GCloud Command Generator with Syntax Validation

This script generates syntactically correct gcloud commands using Gemini 2.5 Pro
and validates them using gcloud's built-in help system.
"""

import subprocess
import json
import re
from typing import Dict, List, Optional, Tuple
import google.generativeai as genai


class GCloudCommandGenerator:
    def __init__(self, gemini_api_key: str, max_iterations: int = 3):
        """
        Initialize the GCloud command generator.
        
        Args:
            gemini_api_key: API key for Gemini
            max_iterations: Maximum number of validation attempts
        """
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel('gemini-2.5-pro-latest')
        self.max_iterations = max_iterations
        
    def _get_gcloud_help(self, partial_command: str) -> Tuple[bool, str]:
        """
        Get help text for a gcloud command to validate its structure.
        
        Args:
            partial_command: The gcloud command (without 'gcloud' prefix)
            
        Returns:
            Tuple of (success, help_text)
        """
        try:
            cmd = f"gcloud {partial_command} --help"
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)
    
    def _validate_command_syntax(self, command: str) -> Tuple[bool, str]:
        """
        Validate gcloud command syntax without executing it.
        
        Args:
            command: Full gcloud command string
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Remove 'gcloud' prefix if present
        if command.strip().startswith('gcloud'):
            command = command.strip()[6:].strip()
        
        # Parse the command to get service and operation
        parts = command.split()
        if len(parts) == 0:
            return False, "Empty command"
        
        # Try to get help for the specific command
        # We'll check progressively: service, service + subcommand, etc.
        for i in range(1, len(parts) + 1):
            # Skip flags/options
            check_parts = [p for p in parts[:i] if not p.startswith('-')]
            if not check_parts:
                continue
                
            partial_cmd = ' '.join(check_parts)
            success, help_text = self._get_gcloud_help(partial_cmd)
            
            if success:
                # Command path exists, now validate the full command structure
                return self._validate_full_command(command, help_text)
        
        return False, f"Invalid gcloud command structure: {command}"
    
    def _validate_full_command(self, command: str, help_text: str) -> Tuple[bool, str]:
        """
        Validate the full command against its help text.
        
        Args:
            command: The command to validate
            help_text: Help text from gcloud
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Extract flags from command
        flags = re.findall(r'--[\w-]+', command)
        
        # Check if flags are valid according to help text
        invalid_flags = []
        for flag in flags:
            # Remove trailing '=' if present
            flag_name = flag.split('=')[0]
            if flag_name not in help_text:
                invalid_flags.append(flag_name)
        
        if invalid_flags:
            return False, f"Invalid flags: {', '.join(invalid_flags)}"
        
        return True, "Command syntax is valid"
    
    def _create_generation_prompt(self, user_prompt: str, previous_error: Optional[str] = None) -> str:
        """
        Create a prompt for Gemini to generate gcloud command.
        
        Args:
            user_prompt: User's description of what they want to do
            previous_error: Error from previous generation attempt
            
        Returns:
            Formatted prompt for Gemini
        """
        base_prompt = f"""You are an expert in Google Cloud Platform and gcloud CLI commands.

Generate a syntactically correct gcloud command based on this request:
{user_prompt}

CRITICAL RULES:
1. Output ONLY the gcloud command, nothing else
2. Use placeholders for actual values:
   - PROJECT_ID for project IDs
   - SERVICE_NAME for service names
   - REGION for regions (or use 'us-central1' as default)
   - INSTANCE_NAME for instance names
   - etc.
3. Ensure the command uses correct gcloud syntax
4. Use the most common and stable command structure
5. Include essential flags only
6. Do NOT add explanations, markdown, or code blocks

Example format:
gcloud run services describe SERVICE_NAME --project=PROJECT_ID --region=REGION

"""
        
        if previous_error:
            base_prompt += f"""
PREVIOUS ATTEMPT FAILED with error:
{previous_error}

Please correct the command and try again.
"""
        
        return base_prompt
    
    def generate_command(self, user_prompt: str, verbose: bool = False) -> Dict:
        """
        Generate and validate a gcloud command.
        
        Args:
            user_prompt: User's description of what they want to do
            verbose: Print detailed validation steps
            
        Returns:
            Dictionary with 'success', 'command', and 'message' keys
        """
        previous_error = None
        
        for iteration in range(self.max_iterations):
            if verbose:
                print(f"\n--- Iteration {iteration + 1} ---")
            
            # Generate command using Gemini
            prompt = self._create_generation_prompt(user_prompt, previous_error)
            
            try:
                response = self.model.generate_content(prompt)
                generated_command = response.text.strip()
                
                # Clean up the response (remove markdown, extra text)
                generated_command = self._clean_command(generated_command)
                
                if verbose:
                    print(f"Generated: {generated_command}")
                
                # Validate the command
                is_valid, message = self._validate_command_syntax(generated_command)
                
                if verbose:
                    print(f"Validation: {'✓ VALID' if is_valid else '✗ INVALID'}")
                    print(f"Message: {message}")
                
                if is_valid:
                    return {
                        'success': True,
                        'command': generated_command,
                        'message': 'Command generated and validated successfully',
                        'iterations': iteration + 1
                    }
                else:
                    previous_error = message
                    
            except Exception as e:
                if verbose:
                    print(f"Error during generation: {str(e)}")
                previous_error = str(e)
        
        return {
            'success': False,
            'command': None,
            'message': f'Failed to generate valid command after {self.max_iterations} attempts',
            'last_error': previous_error
        }
    
    def _clean_command(self, command: str) -> str:
        """
        Clean the generated command from markdown and extra text.
        
        Args:
            command: Raw command from Gemini
            
        Returns:
            Cleaned command string
        """
        # Remove markdown code blocks
        command = re.sub(r'```(?:bash|shell)?\n?', '', command)
        command = re.sub(r'```', '', command)
        
        # Remove common prefixes
        command = re.sub(r'^[$#]\s*', '', command)
        
        # Get first line if multiline
        lines = [line.strip() for line in command.split('\n') if line.strip()]
        
        # Find the line that starts with 'gcloud'
        for line in lines:
            if line.startswith('gcloud'):
                return line
        
        # If no line starts with gcloud, return first non-empty line
        return lines[0] if lines else command.strip()


def main():
    """Example usage of the GCloud Command Generator."""
    import os
    
    # Get API key from environment variable
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("Please set GEMINI_API_KEY environment variable")
        return
    
    # Initialize generator
    generator = GCloudCommandGenerator(api_key)
    
    # Test cases
    test_prompts = [
        "Get the configuration of a Cloud Run service",
        "List all compute engine instances in a project",
        "Describe a Cloud SQL instance",
        "Get logs from a Cloud Run service for the last hour",
        "List all secrets in Secret Manager",
    ]
    
    print("=" * 70)
    print("GCloud Command Generator - Test Cases")
    print("=" * 70)
    
    for prompt in test_prompts:
        print(f"\n📝 Prompt: {prompt}")
        print("-" * 70)
        
        result = generator.generate_command(prompt, verbose=True)
        
        if result['success']:
            print(f"\n✅ SUCCESS!")
            print(f"Command: {result['command']}")
            print(f"Iterations: {result['iterations']}")
        else:
            print(f"\n❌ FAILED!")
            print(f"Message: {result['message']}")
            if 'last_error' in result:
                print(f"Last Error: {result['last_error']}")
        
        print("=" * 70)


if __name__ == "__main__":
    main()